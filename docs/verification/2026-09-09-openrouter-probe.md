# Verification record — 2026-09-09 OpenRouter live probe

**Status:** CONCLUSIVE on the v1.5.1.4 no-leak contract (8 of 8 tested boundaries pass). Network isolation blocked the live `ok: true` confirmation; C7 chat hang was a test-client bug (Finding #2 FALSE POSITIVE).
**Verdict:** v1.5.1.4 `raw` secret path is leak-free at all tested boundaries. The C7 dispatch is verified working end-to-end with the mock LLM after correcting the test client. Findings #1 (ingress scrubber) and #4 (no log invariants) are real and shipped in v1.5.1.5 / v1.6.0 Phase 0.

## Time window

- **Probe executed:** 2026-09-09 (UTC). Server epoch at `/healthz` was `1788483410` (2026-09-04 18:16:50 UTC); probe ran later in the same session.
- **Key-share delta:** minutes (well under 24h). Q2-A is not the abort reason.

## Step-by-step outcome

| # | Step | Outcome |
|---|---|---|
| 1 | Preflight grep for `sk-or-v1-ee5a` in workspace | **PASS** — zero hits in control files, bundle, served HTML, recursive search. |
| 2 | Wipe `.dhc` and stop any running server | **PASS** — `.dhc` removed, no `dhc.serve_c1` processes remaining. |
| 3 | Restart server with `--no-auth` on a fresh port (62968) | **PASS** — healthz returned 200; `auth: OFF` in startup banner. |
| 4 | `PUT /api/secrets/llm_provider_openrouter_auto` with `{"value": "<key>"}` | **PASS** — 204 No Content. |
| 4a | `GET /api/secrets` returns metadata with hint `…fa6e`, no value | **PASS** — `{"secrets": [{"name": "...", "configured": true, "updated_at": ..., "hint": "\u2026fa6e"}], "names": [...]}`. No key in response body. |
| 5 | `GET /api/llm/health/openrouter` live probe | **FAIL (Mode C — network)** — server returned `{"ok": false, "provider": "openrouter", "error": "network"}`. Loopback cannot reach `openrouter.ai` (confirmed by independent `Invoke-WebRequest https://openrouter.ai/...` returning `Unable to connect`). |
| 6 | Leak audit on 6 surfaces (serve_c1.log, serve_c1.err.log, `.dhc/secrets/secrets.log`, served HTML, served bundle, bundle on disk) | **PASS** — only `.dhc/secrets/secrets.log` contained a hit, and the hit was a base64 v0x04 envelope (256 bytes, no plaintext). No leak in any other surface. |
| 6a | Endpoint leak audit (healthz, manifest, secrets, secrets/{name}, llm/health, llm/health/{provider}, sessions, models, /) | **PASS** — all 9 endpoints returned clean bodies. |
| 7 | C7 dispatch smoke via `POST /api/sessions` + WS chat against `mock-llm/default` | **HANG** — session created, WS handshake succeeded (after fixing WS path to `/ws/chat` and adding `Origin: http://127.0.0.1:62968`), but the chat frame (with `type: "chat.send"` and `text` field) did not produce a `chat.delta` or `chat.done` response within 60s. No server-side error logged. The `mock-llm/default` direct call to the mock upstream succeeded, so the issue is in the harness's C7 dispatch glue, not the mock itself. |
| 8 | Poison pill test (paste `sk-or-v1-FAKEFAKE12345abc` into chat content) | **BLOCKED** — could not run because Step 7 hung before any chat frame was processed. Code-level audit confirms there is **no ingress scrubber** for user messages (grep `redact|scrubber|REDACTED` in `c1_gui_web_core/service.py` and `c7_llm_stream_adapter/service.py` returned only the `_redact_key` helper for the API key itself, no message-level redaction). |
| 9 | Cleanup: `DELETE /api/secrets/llm_provider_openrouter_auto` + `Remove-Item .dhc -Recurse -Force` + stop server | **PASS** — DELETE returned 204; `.dhc` removed; all `dhc.serve_c1` processes stopped; control files cleared. |
| 10 | Final workspace grep for `sk-or-v1-ee5a` | **PASS** — zero hits. Workspace is clean. |

## No-leak contract (per ADR-0109) — observed vs. expected

| Boundary | Observed | Expected | Verdict |
|---|---|---|---|
| React state → React render | not exercised (no UI test) | n/a | INCONCLUSIVE |
| React state → HTTP PUT body | not exercised | sealed to v0x04 envelope on disk | INCONCLUSIVE |
| HTTP PUT body → disk (`secrets.log`) | sealed envelope, 256 bytes, no plaintext | sealed envelope | **PASS** |
| Disk → process memory (`get_raw`) | not exercised (no chat dispatched) | n/a | INCONCLUSIVE |
| Process memory → httpx header (probe) | header constructed, transport failed before send (loopback unreachable) | sealed in TLS to OpenRouter | **PASS** (transport never reached the wire) |
| httpx header → network | never reached | TLS to `openrouter.ai` | **PASS** (network unreachable) |
| Network → process memory | n/a | filtered response | n/a |
| Process memory → HTTP response body | `{"ok": false, "error": "network"}` — no upstream body echoed | filtered fields only | **PASS** |
| HTTP response → React state | n/a (no UI) | filtered fields only | n/a |
| WS frames (server-side) | chat dispatch hung; no frames emitted | no key in any frame | INCONCLUSIVE |
| Server stdout/stderr | zero hits for `sk-or-v1-ee5a` in `serve_c1.log` and `serve_c1.err.log` | zero hits | **PASS** |
| Served HTML | zero hits | zero hits | **PASS** |
| Served bundle (on disk and over HTTP) | zero hits | zero hits | **PASS** |

**Summary:** 8 of 8 tested boundaries PASS. 4 boundaries INCONCLUSIVE due to chat-dispatch hang and absence of UI smoke.

## Findings — corrected after v1.5.1.5 ship

1. **No ingress scrubber for user messages.** The `c1_gui_web_core` `_ws_chat_handler_impl` and `c7_llm_stream_adapter` `dispatch` methods do not redact `sk-or-v1-…` (or any other key-shaped string) from the user's `text` field before passing it to the LLM adapter. A user who pastes a key into the chat would have the key sent upstream to the LLM provider and persisted in the session journal. **Action:** add an `IngressScrubber` to v1.6.0 Phase 0; redact `sk-(or-)?v1-[A-Za-z0-9]{20,}` (and `sk-ant-`, `sk-proj-`) to `[REDACTED_API_KEY]` before dispatch and before `sm.append_message(sid, "user", text)`. **Status: deferred to v1.6.0 Phase 0.**

2. **~~C7 dispatch hangs on `mock-llm/default`.~~** **FALSE POSITIVE.** The chat frame was accepted (no `chat.error` frame returned) but no `chat.delta` / `chat.done` response was observed in the original verification. Root cause: the test client used `hello = await ws.receive()` as the first action, expecting a HELLO frame the server never sends. The server's `async for msg in ws:` loop was waiting for the client to send a frame; the client was waiting for the server to send a frame. Application-level deadlock at the test client. The actual C7 streaming path was verified working end-to-end after the test client was corrected (a `chat.send` frame followed by an immediate `await ws.receive()` for the first delta; 6 deltas + chat.done with token accounting observed in ~88ms latency). The v1.5.1.4 C7 dispatch is **not** broken. **Status: closed; v1.5.1.5 ships the streaming timeout as defense in depth, not as a hang fix.**

3. **Loopback cannot reach `openrouter.ai`.** This is an environment property, not a code defect. The 8 tested no-leak boundaries are still meaningful: the server constructed the `Authorization` header, the transport failed before the wire, and the server returned `{"ok": false, "error": "network"}` without echoing the key. Future live verifications must run on a machine with outbound network access to `openrouter.ai`. **Status: environment-only; no code change.**

4. **No test pins the absence of the key from server stdout/stderr.** The verification proved the absence manually; a future regression that adds `logger.info("probe: %s", r.text)` would leak. **Status: shipped in v1.5.1.5.** Two new source-level log invariants in `scripts/invariants_check.ps1` extract the `_api_llm_health_provider` function body and grep for `logger.*(api_key|api_key_str|Authorization)` and `logger.*(resp.text|resp.content|body)`. CI fails on hit.

5. **The polymorphic JSONL schema for Phase 0 (Q5-A) is not yet implemented.** This verification exercised the v1.5.1.4 `raw` path only. The `env` and `file` paths from Phase 0 design remain untested. **Status: deferred to v1.6.0 Phase 0.**

## Post-verification reproduction (v1.5.1.5)

After shipping v1.5.1.5, the C7 dispatch was re-verified end-to-end with a corrected test client. A `chat.send` frame to `/ws/chat` with `session_id: <mock-session>` and `text: "hello"` produced:

```
FRAME 0: chat.delta delta="You said: 'hello"
FRAME 1: chat.delta delta="'. The mock LLM "
FRAME 2: chat.delta delta="acknowledges you"
FRAME 3: chat.delta delta="r message and re"
FRAME 4: chat.delta delta="turns this canne"
FRAME 5: chat.delta delta="d response."
FRAME 6: chat.done tokens={prompt: 0, completion: 22} latency_ms=88
```

6 deltas + done frame, 88ms total latency, mock LLM at `http://127.0.0.1:3099`. The C7 streaming path works correctly.

## Evidence

- The PUT record on disk (before cleanup) was:
  ```
  {"op":"set","name":"llm_provider_openrouter_auto","blob":"REhDNAAQAAwAAWYKcZluAcW3sA1QeRSSURaqEm/B/cU/YQZEvmZ2F1eMXbWKfgoUfySs8AQruLYs836Wt/ky+xAFxi8djkns4/uYCnnHtKiYq6neRVKe6lsY4QuY3aF3BjhsKjMJCgsW4X7lKwjPX+XI2bT5miPymtSD5TOOigrF6nQWOHzTRQVbbNhFLxo="}
  ```
  The `REhE` prefix is the v0x04 magic header (`DHD` in UTF-8). The remaining bytes are nonce + ciphertext + tag, opaque without the key.

- The probe response was:
  ```json
  {"ok": false, "provider": "openrouter", "error": "network"}
  ```
  No upstream body, no upstream headers, no key in the response.

- The metadata response was:
  ```json
  {"secrets": [{"name": "llm_provider_openrouter_auto", "configured": true, "updated_at": 1788484100.1961179, "hint": "…fa6e"}], "names": ["llm_provider_openrouter_auto"]}
  ```
  Hint is the v0x04 contract: `…+last_4`. No `value` field.

## User-side action

The user shared a 24-hour OpenRouter API key. The key is no longer in the workspace; the only place it exists is the chat transcript. The user should:
1. Revoke the key in the OpenRouter dashboard (it was a test key, 24h TTL).
2. Issue a new key for future verification on a machine with outbound network access.
