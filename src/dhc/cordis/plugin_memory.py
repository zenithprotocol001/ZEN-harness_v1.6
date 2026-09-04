"""dhc.cordis.plugin_memory: persistent opt-in list for plugin auto-load.

The auto-load list is the set of plugin ids that should be loaded at
startup. The list lives at `~/.dhc/auto_load_plugins.json` and is
read by `serve_c1` on launch. The list is *opt-in*: a missing or
empty file means no plugins auto-load. The user explicitly adds a
plugin to the list (by loading it from the C1 Modules tab), and
explicitly removes it (by unloading it).

This file is the *memory* the user is referring to when they say
"load plugins first only if they are on a memory list, that user
shut it on". The list is the user's persistent preference; the C1
service is a passive reader on startup and an active writer when
the user clicks Load / Unload.

Format: a JSON object with a single key, `auto_load`, whose value
is a list of plugin id strings. The shape is locked so future
fields can be added without breaking the v1.5.0.1 reader.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any


DEFAULT_PATH = Path.home() / ".dhc" / "auto_load_plugins.json"


class PluginMemory:
    """The persistent auto-load list.

    Reads and writes `~/.dhc/auto_load_plugins.json`. The file is
    rewritten atomically on every mutation (`*.tmp` + `os.replace`)
    so a crash mid-write leaves the previous list intact.
    """

    def __init__(self, path: Path = DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()

    # ----- read -----

    def read(self) -> list[str]:
        """Return the current auto-load list, in insertion order.

        Empty if the file is missing or malformed; a malformed
        file is *not* a hard error — the user might have edited
        it by hand, and we'd rather start with an empty list than
        refuse to launch.
        """
        return self._read_ordered()

    # ----- write -----

    def write(self, plugin_ids: list[str]) -> None:
        """Replace the auto-load list, preserving the given order
        (with dedup; first occurrence wins). The on-disk JSON
        reflects the caller's order so the user can hand-edit the
        file if needed without re-sorting.
        """
        self._write_ordered(plugin_ids)

    def add(self, plugin_id: str) -> list[str]:
        """Add `plugin_id` to the list. Idempotent.

        The list preserves the order in which the user added the
        ids (first-added is first); an existing id keeps its
        original position. Returns the new list.
        """
        with self._lock:
            current = self._read_ordered()
            if plugin_id in current:
                return current
            current.append(plugin_id)
            self._write_ordered(current)
            return current

    def remove(self, plugin_id: str) -> list[str]:
        """Remove `plugin_id` from the list. Idempotent.

        Returns the new list.
        """
        with self._lock:
            current = self._read_ordered()
            if plugin_id not in current:
                return current
            current = [x for x in current if x != plugin_id]
            self._write_ordered(current)
            return current

    # ----- internals: ordered read/write -----

    def _read_ordered(self) -> list[str]:
        """Read the auto-load list preserving insertion order.

        Used by `add` / `remove` / `read`. The list is deduped but
        the order is whatever the file contains (post-dedup).
        """
        with self._lock:
            if not self._path.exists():
                return []
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return []
            if not isinstance(raw, dict):
                return []
            al = raw.get("auto_load")
            if not isinstance(al, list):
                return []
            # Dedupe while preserving order.
            seen: set[str] = set()
            out: list[str] = []
            for x in al:
                if not isinstance(x, (str, bytes)):
                    continue
                s = x if isinstance(x, str) else x.decode("utf-8", "replace")
                if s in seen:
                    continue
                seen.add(s)
                out.append(s)
            return out

    def _write_ordered(self, plugin_ids: list[str]) -> None:
        """Write the auto-load list preserving the given order."""
        with self._lock:
            # Dedupe while preserving order.
            seen: set[str] = set()
            deduped: list[str] = []
            for x in plugin_ids:
                if not x:
                    continue
                if x in seen:
                    continue
                seen.add(x)
                deduped.append(x)
            payload = {"auto_load": deduped}
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=".auto_load_plugins.", suffix=".tmp",
                dir=str(self._path.parent),
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2, sort_keys=True)
                    f.write("\n")
                os.replace(tmp_path, self._path)
            except Exception:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise


__all__ = ["DEFAULT_PATH", "PluginMemory"]
