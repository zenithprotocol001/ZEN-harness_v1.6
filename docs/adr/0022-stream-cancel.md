# ADR-0022: Stream cancel mechanism and its aiohttp limitation

- **Status:** accepted (v1.5.1); follow-up scheduled (v1.5.2)
- **Date:** 2026-09-09
- **Back-references:** ADR-0019 (branching, where the
  in-flight stream-in-progress flag was introduced),
  ADR-0011 (per-session ModelConfig)

## Context

v1.5.1 introduces a user-facing Stop button in the chat
panel. Clicking it should interrupt an in-flight LLM
stream and finalize the assistant turn with the partial
content that had already been streamed. The server side
needs to:

1. Receive the cancel request while the stream is active.
2. Set a flag on the SessionManager.
3. Have the chat handler check the flag between adapter
   chunks and break out of the loop.
4. Persist the partial assistant turn with a `cancelled:
   true` field.
5. Send `chat.cancelled` (ack) and `chat.done` (with
   `cancelled: true`) to the client.

The simplest transport for the cancel is the same WebSocket
the chat stream is running on (`/ws/chat`). A new
`chat.cancel` frame is added to the protocol; the existing
`async for msg in ws:` loop in the chat handler dispatches
it. The challenge: while the chat handler is iterating the
adapter, the outer `async for msg in ws:` loop is suspended.

## Decision

The cancel mechanism in v1.5.1 is:

- A new `chat.cancel` WS frame type, dispatched by the
  outer `async for msg in ws:` loop.
- A new `SessionManager.cancel_stream(sid)` /
  `is_stream_cancelled(sid)` / `clear_cancel(sid)` API on
  the server side.
- A `await asyncio.sleep(0)` yield between adapter chunks
  in the chat handler, so the event loop can deliver any
  pending `chat.cancel` frame to the outer loop's waiter.
- A `cancelled: true` field on the assistant message
  (persisted) and on the `chat.done` frame.
- A `cancelled` chip in the assistant bubble's meta line
  in the chat UI.

The mid-stream interrupt has a known limitation in v1.5.1:
**aiohttp's `WebSocketResponse` enforces "one pending
`receive()` at a time" via a `_waiting` flag.** A concurrent
`receive()` raises `RuntimeError`. The chat handler
therefore cannot race the outer `async for msg in ws:`
loop on the same WebSocket. The `asyncio.sleep(0)` yield
is best-effort: the cancel can interrupt between chunks
in cooperative-scheduling scenarios, but in the worst
case the cancel arrives after `chat.done` has been sent
and is acknowledged with a `noop: true` ack.

## Consequences

- **Stop button works in the common case** for live
  providers that emit at most a few chunks per second
  (OpenAI, Anthropic, OpenRouter). The 10 ms `sleep(0)`
  yield is enough for the event loop to deliver the
  cancel to the outer loop's waiter.
- **Stop button noops** if the stream completes faster
  than the user can click. The client flips Stop → Send
  on the noop ack.
- **v1.5.2 follow-up** is required for a true mid-stream
  interrupt with zero-latency guarantee. Two viable
  designs:
  - **HTTP abort channel**: a separate
    `POST /api/sessions/{sid}/abort` endpoint. The chat
    handler can poll this without touching the WS.
    Simple, low-risk.
  - **Transport refactor**: replace the single-WS
    `async for msg in ws:` pattern with a pair of
    aiohttp channels (one for `chat.send`/downstream, one
    for control/cancel). More invasive.

The v1.5.1 implementation is the lowest-risk option that
ships the Stop button to the user today and lays the
groundwork for v1.5.2's stronger guarantee.

## Compatibility

- `SessionManager.cancel_stream` is additive; no
  existing behavior changes.
- The `chat.cancelled` WS frame is a new type. Clients
  that don't recognize it ignore it (the v1.5.0 client
  just drops unknown types).
- The `cancelled` field on `Message` is optional; old
  session files load unchanged.
- The `cancelled` field on `chat.done` is optional; old
  clients ignore unknown fields.

## Test coverage

- `tests/chat/test_c7_dispatch.py::test_c7_session_manager_cancel_flag_roundtrip`:
  unit-level round-trip of the cancel flag machinery.
- `tests/chat/test_c7_dispatch.py::test_c7_ws_chat_cancel_noop_after_done`:
  end-to-end noop-ack path through the live provider mock.
- The mid-stream test is documented in the test file's
  comment as v1.5.2 work.
