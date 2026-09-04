# ADR-0019: Message branching on the existing `m_<12 hex>` schema

- **Status:** accepted
- **Date:** 2026-09-08
- **Back-references:** ADR-0011 (model config), ADR-0015
  (capability whitelist), ADR-0018 (prompt-path control)

## Context

v1.4.0 (and earlier) the session message ledger is a flat,
append-only list. Every message has a unique `m_<12 hex>` id
generated at append time (`session_manager.py:363`). Branching
is the natural next step: a user reading a session should be
able to fork from any historical message and continue the
conversation in a new direction, leaving the old branch
recoverable but inactive.

The schema is already prepared: the `m_<id>` identifier is
stable, the `append_message(sid, role, content)` API is the
only mutation point, and the session storage is
append-only-then-rewrite-on-update (so the new field can be
added without breaking old session files on disk).

The red-team pass before v1.5.0 raised a real question:
*what is the name of the "currently active" pointer?* I had
proposed `active_leaf_id` but that overloads "leaf." In the
data model, a *leaf* is any node without children, but a
*tip* is the node the user is currently extending. A
`Session.active_tip_id` is the honest name. The query that
enumerates "leaves" (one tip per branch) is a derived view,
not a stored field.

## Decision

The branching model is the smallest one that admits the
three operations a user actually needs (fork, switch,
list). Concretely:

### Data model

- `Message.parent_id: str | None` — the id of the parent
  message. `None` for the root message of a branch. Default
  is `None` so existing linear-history sessions load
  unchanged.
- `Session.active_tip_id: str | None` — the id of the
  message that new appends extend. `None` for an empty
  session. The `active_tip_id` is the *tip* of the active
  branch, not a "leaf" in the data-model sense.
- **Tips** (i.e. messages with no children) are *derived*:
  `tips = {m.id for m in messages if not any(c.parent_id == m.id for c in messages)}`.
  A session with N messages and B branches has B tips (or
  N tips in the linear case where every message is a
  branching point with no children, but in practice the
  most recent messages on each branch are the tips).

### Append rule

```python
def append_message(sid, role, content, *, parent_id=None):
    if parent_id is None:
        parent_id = session.active_tip_id  # None => root
    msg = Message(new_id(), parent_id=parent_id)
    session.messages.append(msg)            # append-only ledger
    session.active_tip_id = msg.id          # tip advances
```

- Normal chat falls out for free: the user message
  extends the tip; the assistant reply then extends the
  tip again (its `parent_id` is the user message).
- Fork = explicit `parent_id` pointing at an earlier node;
  the tip moves to the new branch; the old branch
  survives as a recoverable leaf.
- Root messages (`parent_id=None` on a non-empty session)
  are an error: a fork must point at an existing message.

### Reconstruction

`reconstruct_path(sid, leaf_id) -> list[Message]`:

- Walk `leaf → parent_id → ... → root`, return the
  reversed list. The leaf is the *end* of the path, the
  root is the *start*.
- **Max-depth guard: 256.** A path of 257 messages raises
  `BranchDepthExceeded`. The guard prevents an attacker
  (or a bug) from constructing a cycle that would make
  the path walk infinite.

### Stream pinning

When an assistant reply is in flight (the WS stream
handler or the HTTP message endpoint), the user may click
"Fork" on a *different* historical message. The in-flight
assistant reply must be appended as a child of the
*original* user message, not the current tip after the
fork.

**Rule:** the assistant's `parent_id` is captured at the
start of the stream, not at the end. Concretely: the
client passes `parent_id` in the request body (or, if
omitted, the server uses the user message's `parent_id`).
The server pins the final assistant message to that
`parent_id` regardless of intervening `branch.switch`
calls.

### Server-side stream guard

While a stream is active on a session, `branch.switch` is
**refused** with `409 Conflict` and a
`{"error": "stream_in_progress"}` body. The frontend may
keep the Fork button enabled; the server enforces the
invariant. The WebSocket handler sets a per-session
`stream_in_progress: bool` flag; the `branch.switch`
handler reads it. The flag is cleared in the `finally`
block of the stream handler.

The frontend lockout is a v1.5.1 nicety; the server guard
is the v1.5.0 contract.

### Routes

Three new C1 routes, all gated:

- `POST /api/sessions/{sid}/branches` →
  `BRANCH_CREATE`. Body: `{parent_message_id: "m_..."}`.
  Returns: `{branch_root: "m_...", active_tip: "m_..."}`.
- `PATCH /api/sessions/{sid}/active-tip` →
  `BRANCH_SWITCH`. Body: `{leaf_message_id: "m_..."}`.
  Returns: `204` on success, `409` if a stream is in
  progress, `404` if the leaf is not in the session.
- `GET /api/sessions/{sid}/branches` → `BRANCH_LIST`.
  Returns: `{tips: [{"id": "m_...", "path": [m1, m2, ...]}, ...]}`.
  Each entry is a tip + its full reconstructed path.

### `usage_totals` semantics

`Session.usage_totals` remains the **branch-agnostic sum**
across all messages in the session (v1.4.0 behavior). The
prompt and completion token counts of a forked-off
branch are still counted against the session total; the
active-tip switch does not reset or partition the totals.
Per-branch totals are a v1.6.0 metric addition.

## What this ADR does NOT do

- It does not introduce a per-branch cost limit. The
  session-level `usage_totals` is the only cost boundary.
- It does not introduce branch archiving or pruning. A
  session accumulates all branches until the
  1000-message cap (v1.2.0) truncates oldest-first. A
  future v1.6.0 may add per-branch archival.
- It does not introduce branch sharing. Branches are
  per-session; cross-session branching is a separate
  feature.
- It does not introduce auto-pruning of dead branches.
  Every branch survives until the message cap kicks in.

## Consequences

- Three new capabilities added to the C1 surface:
  `BRANCH_CREATE`, `BRANCH_SWITCH`, `BRANCH_LIST`. Total
  capabilities: 20 (v1.4.0) + 6 (v1.5.0 branching +
  attachments) = 26. Within the v1.5.0 cap of 32
  (ADR-0015-amendment-1).
- `Message.parent_id` is `Optional[str]` with default
  `None`. Existing session files on disk load unchanged:
  the field is filled with `None` by `SessionManager.get()`.
- `Session.active_tip_id` is `Optional[str]` with default
  `None`. For old session files, the field is filled by
  the first `append_message` call (the first append sets
  `active_tip_id` to the new message id).
- The mock LLM (v1.2.0) is unchanged. The branching model
  is orthogonal to provider dispatch.
- The audit log gains two new event types:
  `branch.create` and `branch.switch`. They carry the
  same `audit_text` projection as the v1.4.0 events.
