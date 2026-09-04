"""dhc.services.session_manager: persistent chat session storage.

A `Session` is a single conversation between the user and the LLM.
It owns:
  - a stable `id` (auto-generated, format: `s_<16 hex>`)
  - a `title` (auto-generated from the first user message, or user-set)
  - the list of `messages` (user/assistant/tool turns, each with
    content + timestamp + optional tool_calls and token counts)
  - metadata: `tags`, `pinned`, `archived`, `model` (the LLM model
    name the user picked, e.g. "gpt-4o"), `created_at`, `updated_at`
  - v1.5.0 (ADR-0019): `active_tip_id` (the message that
    `append_message` extends when no explicit `parent_id` is given),
    and the message-level `parent_id` field that forms the tree.

Storage:

    ~/.dhc/sessions/{id}.json
    + a sidecar `~/.dhc/sessions/index.json` that maps `updated_at`
      order to session ids so the session list endpoint does not
      have to stat() every file every time.

Atomicity:

    Every write goes through `_atomic_write_json`, which writes to a
    `*.tmp` file and `os.replace`s it onto the target. On a crash
    mid-write, the original file is intact.

Size cap:

    A session may grow to at most `MAX_MESSAGES_PER_SESSION` (1000
    by default). When a session is full, the OLDEST message is
    dropped (a `truncated: true` flag is set on the message that
    took its place). This is documented in `docs/session-storage.md`.

Concurrency:

    The manager holds a single re-entrant lock for all mutations.
    Reads are lock-free and use a small LRU.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Any


MAX_MESSAGES_PER_SESSION = 1000
TITLE_WORD_COUNT = 8
TITLE_MAX_CHARS = 60
BRANCH_MAX_DEPTH = 256  # ADR-0019: max path length per branch

VALID_ROLES = frozenset({"user", "assistant", "system", "tool"})

_ID_ALPHABET = "0123456789abcdef"


def _new_id() -> str:
    return "s_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(16))


def _autotitle(text: str) -> str:
    """Generate a short, human-readable session title from the first
    user message. Strips newlines, collapses whitespace, takes the
    first 8 words or 60 characters, whichever is shorter.
    """
    if not text:
        return "New session"
    flat = re.sub(r"\s+", " ", text).strip()
    words = flat.split(" ")
    if len(words) > TITLE_WORD_COUNT:
        flat = " ".join(words[:TITLE_WORD_COUNT]).rstrip(",.;:")
    if len(flat) > TITLE_MAX_CHARS:
        flat = flat[:TITLE_MAX_CHARS].rsplit(" ", 1)[0] or flat[:TITLE_MAX_CHARS]
    return flat or "New session"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    """Atomically write a JSON file: write to `*.tmp`, then rename.

    v1.5.1.2 (audit hotfix): the parent directory is re-created
    on every write if it is missing. The constructor's
    `mkdir(parents=True, exist_ok=True)` only runs at startup; if
    the on-disk layout is mutated after startup (a test cleanup,
    the v0x03→v0x04 migration that touched this directory, or a
    manual `rm`), the next write would raise `FileNotFoundError`
    and POST /api/sessions would 500. Self-healing on every write
    makes the path robust to any prior state.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


class BranchDepthExceeded(Exception):
    """Raised when a path reconstruction exceeds `BRANCH_MAX_DEPTH`.

    This is defense against a cycle in the parent_id chain (a bug or
    a tampered session file). A legitimate session never approaches
    this depth.
    """


class Session:
    def __init__(
        self,
        id: str,
        title: str,
        created_at: int,
        updated_at: int,
        messages: list[dict[str, Any]],
        model: str = "",
        tags: list[str] | None = None,
        pinned: bool = False,
        archived: bool = False,
        usage_totals: dict[str, int] | None = None,
        active_tip_id: str | None = None,
    ) -> None:
        self.id = id
        self.title = title
        self.created_at = created_at
        self.updated_at = updated_at
        self.messages = messages
        self.model = model
        self.tags = list(tags) if tags else []
        self.pinned = pinned
        self.archived = archived
        # v1.3.2: per-session token aggregation. The shape is locked:
        #   prompt_tokens, completion_tokens, total_tokens: lifetime
        #   last_turn_prompt, last_turn_completion: per-turn values
        # Sessions written by v1.2.x / v1.3.0 / v1.3.1 are missing
        # this field; `from_dict` fills it in with zeros so the field
        # is always present in the API response.
        self.usage_totals: dict[str, int] = (
            dict(usage_totals) if usage_totals is not None else
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "last_turn_prompt": 0,
                "last_turn_completion": 0,
            }
        )
        # v1.5.0 (ADR-0019): the message that new appends extend when
        # no explicit `parent_id` is given. None for an empty session.
        # Sessions written by v1.4.0 and earlier are missing this
        # field; `from_dict` fills it in lazily on the first
        # `append_message` call after the load.
        self.active_tip_id: str | None = active_tip_id

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "messages": self.messages,
            "model": self.model,
            "tags": self.tags,
            "pinned": self.pinned,
            "archived": self.archived,
            "usage_totals": self.usage_totals,
        }
        if self.active_tip_id is not None:
            d["active_tip_id"] = self.active_tip_id
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Session":
        raw_totals = d.get("usage_totals") or {}
        totals: dict[str, int] = {
            "prompt_tokens": int(raw_totals.get("prompt_tokens") or 0),
            "completion_tokens": int(raw_totals.get("completion_tokens") or 0),
            "total_tokens": int(raw_totals.get("total_tokens") or 0),
            "last_turn_prompt": int(raw_totals.get("last_turn_prompt") or 0),
            "last_turn_completion": int(raw_totals.get("last_turn_completion") or 0),
        }
        s = cls(
            id=str(d["id"]),
            title=str(d.get("title") or "New session"),
            created_at=int(d.get("created_at") or 0),
            updated_at=int(d.get("updated_at") or 0),
            messages=list(d.get("messages") or []),
            model=str(d.get("model") or ""),
            tags=list(d.get("tags") or []),
            pinned=bool(d.get("pinned") or False),
            archived=bool(d.get("archived") or False),
            usage_totals=totals,
            active_tip_id=d.get("active_tip_id"),
        )
        # v1.5.0 (ADR-0019): backfill `parent_id` on every message
        # that is missing it. Old session files have no `parent_id`
        # field; the messages are a linear history, so every
        # message's parent is the previous one (or None for the
        # first message).
        s._backfill_parent_ids()
        # And the active_tip_id, if absent, is the last message's id
        # (linear-history fallback).
        if s.active_tip_id is None and s.messages:
            s.active_tip_id = str(s.messages[-1].get("id") or "")
        return s

    def _backfill_parent_ids(self) -> None:
        """Set `parent_id` on every message that is missing it.

        Old session files (v1.4.0 and earlier) have no `parent_id`
        field. We treat the messages as a linear history: every
        message's parent is the previous one in the list (or None
        for the first message). The mutation is in-place.
        """
        prev_id: str | None = None
        for m in self.messages:
            if "parent_id" not in m or m.get("parent_id") is None and prev_id is not None:
                # Only set if not already set; do not clobber an
                # explicit `parent_id: None` on the first message.
                if "parent_id" not in m:
                    m["parent_id"] = prev_id
            prev_id = str(m.get("id") or prev_id or "") or prev_id

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": len(self.messages),
            "model": self.model,
            "tags": self.tags,
            "pinned": self.pinned,
            "archived": self.archived,
            "usage_totals": self.usage_totals,
            "active_tip_id": self.active_tip_id,
        }


class SessionManager:
    """Persistent chat-session storage with atomic writes.

    The constructor takes a directory; the manager creates the
    `sessions/` and `index.json` files lazily on first use.
    """

    def __init__(self, root_dir: Path, max_messages: int = MAX_MESSAGES_PER_SESSION) -> None:
        self._root = Path(root_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._dir = self._root / "sessions"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self._root / "sessions-index.json"
        self._max_messages = max_messages
        self._lock = threading.RLock()
        # In-memory cache: id -> Session. Populated on first read.
        self._cache: dict[str, Session] = {}
        # v1.5.0 (ADR-0019): per-session stream-in-progress flag.
        # The flag is set by the WS / HTTP chat handler when a
        # stream starts and cleared in the `finally` block. The
        # `branch.switch` route refuses with 409 if the flag is
        # set. This is a per-process flag; in a multi-process
        # deployment it would be replaced with a per-session
        # distributed lock.
        self._stream_in_progress: set[str] = set()
        # v1.5.1: per-session cancel flag. Set by the WS handler on
        # receipt of `chat.cancel`; checked by the chat handler
        # between stream chunks; cleared in the same `finally`
        # block that clears `_stream_in_progress`.
        self._cancel_requested: set[str] = set()

    # ----- create / read / update / delete -----

    def create(self, title: str | None = None, model: str | None = None) -> Session:
        with self._lock:
            now = int(time.time() * 1000)
            s = Session(
                id=_new_id(),
                title=title or "New session",
                created_at=now,
                updated_at=now,
                messages=[],
                # v1.5.1.4 (audit hotfix #4): the chat
                # header renders a "Default model" picker
                # when no session is active. The user's
                # pick is stored in localStorage and sent
                # as `{model: "..."}` in the POST body when
                # creating a new session. Empty string is
                # the legacy default ("no model set yet")
                # — the C7 dispatch falls back to the
                # harness-level default at that point.
                model=model or "",
            )
            self._save(s)
            self._cache[s.id] = s
            return s

    def get(self, session_id: str) -> Session | None:
        with self._lock:
            if session_id in self._cache:
                return self._cache[session_id]
            path = self._dir / f"{session_id}.json"
            if not path.exists():
                return None
            s = Session.from_dict(json.loads(path.read_text(encoding="utf-8")))
            self._cache[session_id] = s
            return s

    def list_summaries(
        self,
        include_archived: bool = False,
        search: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """List session summaries sorted by `pinned desc, updated_at desc`.

        `search` matches if it appears in the title or any message
        content (case-insensitive). `limit` caps the number of
        summaries returned (no effect on side effects).
        """
        with self._lock:
            self._ensure_index()
            index = self._load_index()
            results: list[dict[str, Any]] = []
            needle = (search or "").strip().lower()
            for sid in index.get("ids", []):
                s = self.get(sid)
                if s is None:
                    continue
                if not include_archived and s.archived:
                    continue
                if needle:
                    hay = s.title.lower() + "\n" + "\n".join(
                        str(m.get("content") or "") for m in s.messages
                    )
                    if needle not in hay.lower():
                        continue
                results.append(s.summary())
                if limit is not None and len(results) >= limit:
                    break
            # Sort: pinned first, then by updated_at desc.
            results.sort(key=lambda r: (not r.get("pinned", False), -r["updated_at"]))
            return results

    def search(
        self,
        query: str,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[Session]:
        """Case-insensitive search across session title and message content.

        Returns full ``Session`` objects (not summaries) ordered by
        ``updated_at`` desc, with pinned sessions floated to the top.
        Archived sessions are excluded unless ``include_archived`` is
        true. An empty/whitespace query returns an empty list; callers
        should fall back to ``list_summaries`` for the full listing.
        """
        with self._lock:
            self._ensure_index()
            index = self._load_index()
            needle = (query or "").strip().lower()
            if not needle:
                return []
            matched: list[Session] = []
            for sid in index.get("ids", []):
                if len(matched) >= limit:
                    break
                s = self.get(sid)
                if s is None:
                    continue
                if not include_archived and s.archived:
                    continue
                hay = s.title.lower() + "\n" + "\n".join(
                    str(m.get("content") or "") for m in s.messages
                )
                if needle in hay:
                    matched.append(s)
            matched.sort(
                key=lambda s: (not s.pinned, -s.updated_at)
            )
            return matched

    def update(
        self,
        session_id: str,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
        tags: list[str] | None = None,
        model: str | None = None,
        usage_totals: dict[str, int] | None = None,
        active_tip_id: str | None = None,
    ) -> Session | None:
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return None
            if title is not None:
                s.title = title
            if pinned is not None:
                s.pinned = bool(pinned)
            if archived is not None:
                s.archived = bool(archived)
            if tags is not None:
                s.tags = list(tags)
            if model is not None:
                s.model = model
            if usage_totals is not None:
                s.usage_totals = dict(usage_totals)
            if active_tip_id is not None:
                s.active_tip_id = active_tip_id
            s.updated_at = int(time.time() * 1000)
            self._save(s)
            return s

    def soft_delete(self, session_id: str) -> bool:
        return self.update(session_id, archived=True) is not None

    def hard_delete(self, session_id: str) -> bool:
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return False
            path = self._dir / f"{session_id}.json"
            if path.exists():
                path.unlink()
            self._cache.pop(session_id, None)
            self._ensure_index()
            index = self._load_index()
            index["ids"] = [i for i in index.get("ids", []) if i != session_id]
            _atomic_write_json(self._index_path, index)
            return True

    # ----- message operations -----

    def append_message(
        self,
        session_id: str,
        role: str,
        content: str,
        tool_calls: list[dict[str, Any]] | None = None,
        tokens: dict[str, int] | None = None,
        auto_title: bool = True,
        parent_id: str | None = None,
    ) -> dict[str, Any] | None:
        if role not in VALID_ROLES:
            raise ValueError(f"invalid role {role!r}; must be one of {sorted(VALID_ROLES)}")
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return None
            now = int(time.time() * 1000)
            message_id = "m_" + "".join(secrets.choice(_ID_ALPHABET) for _ in range(12))
            # v1.5.0 (ADR-0019): if no explicit parent_id is given,
            # the new message extends the session's active_tip_id
            # (or is the root if the session is empty).
            if parent_id is None:
                parent_id = s.active_tip_id
            msg = {
                "id": message_id,
                "role": role,
                "content": content,
                "ts_ms": now,
                "parent_id": parent_id,
            }
            if tool_calls:
                msg["tool_calls"] = tool_calls
            if tokens:
                msg["tokens"] = tokens
            s.messages.append(msg)
            # The new message is the new tip.
            s.active_tip_id = message_id
            # Cap: drop oldest non-system message if we exceed.
            truncated = False
            while len(s.messages) > self._max_messages:
                # find first non-system message
                idx = next(
                    (i for i, m in enumerate(s.messages) if m.get("role") != "system"),
                    None,
                )
                if idx is None:
                    break
                s.messages.pop(idx)
                truncated = True
            if truncated:
                # The first non-system message gets the flag.
                for m in s.messages:
                    if m.get("role") != "system":
                        m["truncated"] = True
                        break
            # Auto-title from the first user message.
            if auto_title and s.title in ("", "New session") and role == "user" and content:
                s.title = _autotitle(content)
            s.updated_at = now
            self._save(s)
            return msg

    # ----- branching (v1.5.0, ADR-0019) -----

    def reconstruct_path(
        self,
        session_id: str,
        leaf_id: str,
    ) -> list[dict[str, Any]]:
        """Walk `leaf_id → parent_id → ... → root` and return the
        reversed list (root first, leaf last).

        The max-depth guard (`BRANCH_MAX_DEPTH`, default 256)
        prevents infinite loops on a tampered or buggy session.
        Raises `BranchDepthExceeded` if the guard trips.

        Returns an empty list if the session is unknown.
        """
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return []
            by_id = {
                str(m.get("id") or ""): m
                for m in s.messages
                if m.get("id")
            }
            if leaf_id not in by_id:
                return []
            path: list[dict[str, Any]] = []
            cur: str | None = leaf_id
            depth = 0
            while cur is not None:
                if depth > BRANCH_MAX_DEPTH:
                    raise BranchDepthExceeded(
                        f"branch depth exceeds {BRANCH_MAX_DEPTH} "
                        f"at message {cur!r}"
                    )
                m = by_id.get(cur)
                if m is None:
                    return []  # parent_id points to a non-existent message
                path.append(m)
                cur = m.get("parent_id")  # may be None at the root
                depth += 1
            path.reverse()
            return path

    def list_branches(self, session_id: str) -> list[dict[str, Any]]:
        """Enumerate the branches (tips) of a session.

        A tip is a message with no children. Each entry is
        `{"id": "m_...", "path": [m1, m2, ...]}` where `path` is
        the reconstructed chain from the root to the tip. The
        active_tip_id is marked with `active: true`.

        Returns an empty list if the session is unknown.
        """
        with self._lock:
            s = self.get(session_id)
            if s is None:
                return []
            has_children: set[str] = set()
            for m in s.messages:
                pid = m.get("parent_id")
                if pid:
                    has_children.add(str(pid))
            tips = [
                m for m in s.messages
                if str(m.get("id") or "") not in has_children
            ]
            out: list[dict[str, Any]] = []
            for tip in tips:
                tip_id = str(tip.get("id") or "")
                path = self.reconstruct_path(session_id, tip_id)
                out.append(
                    {
                        "id": tip_id,
                        "active": tip_id == s.active_tip_id,
                        "path": path,
                    }
                )
            # Sort: active first, then by the tip's path length
            # (the most-recently-touched branch first), then by
            # message id for stability.
            out.sort(
                key=lambda t: (
                    not t["active"],
                    -len(t["path"]),
                    t["id"],
                )
            )
            return out

    def branch_create(
        self,
        session_id: str,
        parent_message_id: str,
        role: str = "user",
        content: str = "",
        auto_title: bool = True,
    ) -> dict[str, Any] | None:
        """Create a new branch by appending a message whose
        `parent_id` is `parent_message_id`.

        The new message's id becomes the active_tip_id. The old
        branch (whose tip was the previous active_tip_id) is
        preserved as a recoverable leaf.

        Returns the new message dict, or `None` if the session or
        parent message is unknown. Raises `ValueError` for an
        invalid role.
        """
        if role not in VALID_ROLES:
            raise ValueError(f"invalid role {role!r}; must be one of {sorted(VALID_ROLES)}")
        # The append_message call goes through the normal path with
        # an explicit parent_id, so the new message becomes the
        # tip.
        return self.append_message(
            session_id=session_id,
            role=role,
            content=content,
            parent_id=parent_message_id,
            auto_title=auto_title,
        )

    def branch_switch(
        self,
        session_id: str,
        leaf_message_id: str,
    ) -> Session | None:
        """Set the session's `active_tip_id` to `leaf_message_id`.

        Refuses (returns `None`) if a stream is in progress on the
        session, or if `leaf_message_id` is not a message in the
        session. The 409 is reported by the route handler; the
        manager just returns `None` on refusal.
        """
        with self._lock:
            if session_id in self._stream_in_progress:
                return None
            s = self.get(session_id)
            if s is None:
                return None
            ids = {str(m.get("id") or "") for m in s.messages}
            if leaf_message_id not in ids:
                return None
            s.active_tip_id = leaf_message_id
            s.updated_at = int(time.time() * 1000)
            self._save(s)
            return s

    # ----- stream-in-progress flag (v1.5.0, ADR-0019) -----

    def begin_stream(self, session_id: str) -> None:
        """Mark a stream as in progress on the session.

        Called by the chat handler at the start of a stream. The
        `branch_switch` route refuses while the flag is set.
        """
        with self._lock:
            self._stream_in_progress.add(session_id)

    def end_stream(self, session_id: str) -> None:
        """Clear the stream-in-progress flag.

        Called by the chat handler in its `finally` block. Safe
        to call even if the flag was never set.
        """
        with self._lock:
            self._stream_in_progress.discard(session_id)

    def is_stream_in_progress(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._stream_in_progress

    # ----- v1.5.1: cancel flag (ADR-0022 draft) -----
    # When a stream is running, the chat handler checks
    # `is_stream_cancelled(sid)` between chunks. The WS handler
    # sets the flag on receipt of a `chat.cancel` frame and clears
    # it when the stream ends. Calling `cancel_stream` on a
    # session that is not streaming is a no-op.

    def cancel_stream(self, session_id: str) -> None:
        with self._lock:
            self._cancel_requested.add(session_id)

    def is_stream_cancelled(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._cancel_requested

    def clear_cancel(self, session_id: str) -> None:
        with self._lock:
            self._cancel_requested.discard(session_id)

    # ----- internals -----

    def _save(self, s: Session) -> None:
        path = self._dir / f"{s.id}.json"
        _atomic_write_json(path, s.to_dict())
        # Update the index.
        self._ensure_index()
        index = self._load_index()
        ids = list(index.get("ids", []))
        if s.id not in ids:
            ids.append(s.id)
        index["ids"] = ids
        _atomic_write_json(self._index_path, index)

    def _load_index(self) -> dict[str, Any]:
        if not self._index_path.exists():
            return {"ids": []}
        return json.loads(self._index_path.read_text(encoding="utf-8"))

    def _ensure_index(self) -> None:
        """Rebuild the index from disk if it's missing or stale.

        Stale means: an id appears in the index but the file is gone,
        or a session file exists on disk but is not in the index.
        """
        index = self._load_index()
        indexed = set(index.get("ids", []))
        on_disk = {p.stem for p in self._dir.glob("*.json") if not p.name.endswith(".tmp")}
        # Always rebuild from disk to be consistent.
        if indexed != on_disk:
            new_ids = sorted(on_disk)
            _atomic_write_json(self._index_path, {"ids": new_ids})


__all__ = [
    "BRANCH_MAX_DEPTH",
    "BranchDepthExceeded",
    "MAX_MESSAGES_PER_SESSION",
    "Session",
    "SessionManager",
    "TITLE_MAX_CHARS",
    "TITLE_WORD_COUNT",
]
