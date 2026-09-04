# GLOSSARY

| Term | Definition |
|---|---|
| **Cordis** | The TypeScript framework for spatiotemporal composability that the DHC harness ports to Python. |
| **Context** | The Cordis service registry + event bus + disposable stack. One per harness instance. |
| **EventEmitter** | The Cordis event bus. Supports `on`, `off`, `emit`, and `waterfall`. |
| **waterfall** | An event where each listener receives the previous listener's return value and may mutate it. |
| **dispose** | The cleanup function a plugin returns from `apply`. Registered on the context's disposable stack; run in reverse on `ctx.dispose()`. |
| **`@plugin`** | The decorator that turns a plain `apply(ctx, config)` function into a Cordis plugin. |
| **manifest** | A pydantic-validated JSON file describing a plugin: `id`, `name`, `version`, `entrypoint`, `events`, `config_schema`, `sha256`. |
| **DHC-V** | The DeepSeek Harness Creation Value score: `functionality * (security / 100)`, with a hard floor at `security < 50`. |
| **functionality_score** | `unit_pass_rate * 40 + turn_completion_rate * 40 + ui_streaming_fidelity * 20`. Re-weighted to 50/50 if Playwright is unavailable. |
| **security_score** | Starts at 100; deducts per `Finding`. `critical` floors the score. |
| **floor_triggered** | `True` iff a `critical` finding is present or the post-deduction security score is `< 50`. |
| **`production_ready`** | DHC-V ≥ 80. |
| **`experimental`** | DHC-V 50 – 79. |
| **`unsafe`** | DHC-V < 50. |
| **C1..C10** | The 10 core modules. See `docs/architecture.md`. |
| **tool guard** | C4. Schema-strict tool invocation with security checks. |
| **capability policy** | C9. Deny-all default; intercepts `tools/pre-execute`. |
| **`pre-execute`** | The Cordis event fired before any tool call; C9 listens to it. |
| **boundary tokens** | C3. The 9 special tokens escaped before the user section is wrapped: `<|user_start|>`, `<|user_end|>`, etc. |
| **HMAC-SHA256** | The keyed hash used in C5 (agent manifest) and C8 (webhook). |
| **`compare_digest`** | The constant-time comparison from `hmac.compare_digest`. The **only** legal way to compare HMACs. |
| **nonce** | A unique value sent with a webhook to defeat replay. C8 stores them in a bounded LRU. |
| **timestamp window** | ±5 minutes. Webhooks outside this window are rejected as `ExpiredTimestamp`. |
| **replay** | Re-submission of a previously seen webhook. Defeated by the nonce store. |
| **loopback** | `127.0.0.1`. C1 only binds to loopback. |
| **bearer token** | A 256-bit secret in `serve_c1.token`; required on every WS handshake and HTTP request to C1. |
| **CSP** | Content-Security-Policy. The HTTP header that tells the browser what is allowed to load. |
| **DOMPurify** | The XSS sanitizer used in the web client. |
| **FORBID_TAGS** | The list of tags DOMPurify will strip: `script`, `style`, `iframe`, `object`, `embed`, `form`, `input`. |
| **`dangerouslySetInnerHTML`** | React's escape hatch for raw HTML. Only used in `panels/EventsPanel.tsx`, always after DOMPurify. |
| **ephemeral port** | A port picked by the OS at runtime. C1 picks one, writes it to `serve_c1.port`. |
| **XSS** | Cross-Site Scripting. The attack class that CSP + DOMPurify + the negative invariant defend against. |
| **RCE** | Remote Code Execution. The attack class that the manifest SHA pin + the `BashInput.command` schema defend against. |
| **supply chain** | The path from plugin author to load time. Defended by the SHA-256 pin. |
| **SHA-256 pin** | The 64-hex digest of `service.py` stored in `manifest.json` and verified at load time. |
| **manifest integrity** | The property that the on-disk `service.py` matches the SHA in `manifest.json`. |
| **paste-and-score** | The browser feature that lets you paste LLM output and run it through the eval pipeline. |
| **offline eval** | The `run_llm_eval.py` wrapper that runs the full 10-prompt eval without network access. |
| **master prompt** | One of the 10 prompts in `src/dhc/eval/prompts/`. Each is the rubric for one C-module. |
| **waterfall event** | An event whose value flows through listeners and may be mutated. `agent/pre-step` is the canonical example. |
| **`turn/start`** | The first event in a turn. |
| **`step/start`** | Emitted at the beginning of each step. |
| **`llm/stream`** | Emitted for every chunk of the LLM response. |
| **`tool/call`** | Emitted when the LLM decides to invoke a tool. |
| **`step/end`** | Emitted at the end of each step. |
| **`turn/end`** | The last event of a turn. `reason` ∈ `ABORT_REASONS`. |
| **`ABORT_REASONS`** | `completed`, `max_steps_exceeded`, `tool_error`, `policy_denied`, `llm_error`. |
| **mock LLM** | The deterministic aiohttp server in `fixtures/mock_llm/` that pretends to be an OpenAI-compatible provider. |
| **frozen epoch** | `FROZEN_EPOCH_MS` in `fixtures/mock_llm/scripts.py`. All timestamps in tests are pinned to `2026-01-01T00:00:00Z`. |
| **evaluator** | The thing being scored. Usually an LLM producing a plugin module. |
| **`Finding`** | The dataclass `dhc.scoring.scorer.Finding(module, severity, description)`. |
| **`ModuleScore`** | The dataclass `dhc.scoring.scorer.ModuleScore(module, functionality, security, findings, notes)`. |
| **relay** | The `relay/` folder where the versioned zip artifacts live. |
| **invariants** | The PowerShell-based static checks in `scripts/invariants_check.ps1`. 92 of them at v1.1.0. |
| **static check** | The set of PowerShell scripts that verify the harness without running Python. |
| **envelope** | The encrypted at-rest blob in `~/.dhc/secrets/secrets.log`. Format: 4-byte header (`DHC1`, `DHC2`, or `DHC3`) + (extension block for `DHC3`) + 16-byte nonce + ciphertext + 32-byte tag. The v0x03 tag is over `header ‖ ext_area ‖ nonce ‖ name ‖ ciphertext` (v1.3.3+); v0x01/v0x02 tags do not include the name. |
| **per-secret nonce** | The 16 random bytes stored in the envelope header slot. Used as the scrypt KDF salt in `DHC2` envelopes (v1.3.1+). In v0x03 (`DHC3`) the KDF salt is `strict_nonce (12) ‖ aead_nonce (16)` (28 bytes) — two independent random per-secret values. |
| **model config** | Per-session LLM parameters (temperature, max_tokens, top_p, system_prompt) stored encrypted in `SecretsService` under the key `model_config_{session_id}`. |
| **token usage** | The `prompt_tokens` + `completion_tokens` counts returned by an LLM provider. Surfaced in v1.3.1 via the `usage` field on the final `StreamChunk`; aggregated across turns in v1.3.2 in `Session.usage_totals`. |
| **`DHC1`** | The v0x01 envelope header. Fixed scrypt salt; read-only since v1.3.1. |
| **`DHC2`** | The v0x02 envelope header. Per-envelope nonce as scrypt salt; default since v1.3.1, default in v1.3.2 only for legacy writes. |
| **`DHC3`** | The v0x03 envelope header. Extension blocks between the header and the AEAD nonce. Default since v1.3.2. Read-only since v1.3.4 (canonical v0x04 envelopes are the new default). |
| **`DHC4`** | The v0x04 envelope header. Same wire format as v0x03 but with a canonical MAC input that length-prefixes the secret name with `u32 BE`. Default since v1.3.4. |
| **extension block** | A length-prefixed metadata block in a v0x03 envelope. Format: `[ext_len:2 BE][ext_type:2 BE][ext_body:ext_len]`. Extension type `0x0001` is the strict per-secret nonce. Unknown types are skipped (forward compat). |
| **strict per-secret nonce** | The 12 random bytes stored as extension type `0x0001` inside a v0x03 envelope. Combined with the 16-byte AEAD nonce to form a 28-byte scrypt KDF salt. |
| **usage totals** | The per-session aggregate `{"prompt_tokens", "completion_tokens", "total_tokens", "last_turn_prompt", "last_turn_completion"}` carried on the `Session` model (v1.3.2+). |
| **name binding** | The v1.3.3+ property that a v0x03 envelope's tag authenticates the secret name alongside the ciphertext. A valid v0x03 envelope stored under the wrong name fails the tag check. Enforced for v0x03 only; v0x01 and v0x02 envelopes do not bind the name. |
| **canonicalization** | Encoding variable-length fields with explicit length prefixes so the serialized byte layout is unambiguous. The v0x04 envelope (ADR-0014) length-prefixes the `name` field with `u32 BE` in the MAC input. |
| **field-boundary shift** | An attack that moves bytes between adjacent variable-length fields in a raw concatenation, leaving the total byte string (and any MAC over it) unchanged. The v0x03 envelope's `name ‖ ct` boundary is vulnerable; v0x04 closes the seam with a `len(name):u32` prefix. |
| **bound / unbound envelope** | A v0x03+ envelope whose MAC does / does not include the secret name. Unbound is only produced by migration primitives with `require_binding=False`. |
| **Entropy Gate** | The v1.4.0 component (codename CREC) that enforces a finite, enumerable action space. Combines the capability whitelist (ADR-0015) and the 62-code legal-charset Number Gate (ADR-0016). One policy, three projections (runtime enforcer, test generator, Entropy Map). |
| **capability** | A named action on the C1 surface, registered in `ACTION_WHITELIST`. Default-deny. Adding a capability requires an ADR + a whitelist entry + its pass/deny tests. |
| **whitelist** | The `ACTION_WHITELIST: dict[Capability, ActorTier]` table. Single source of truth for the runtime enforcer, the test generator, and the Entropy Map. |
| **Number Gate** | The v1.4.0 canonicalization layer that maps any byte sequence to a list of integer codes (`to_codes`) and projects codes to text via the 62-code legal alphabet (`from_codes`). Lossless at storage; bounded at the audit-render boundary. |
| **legal charset** | The 62 codes `[A-Z a-z 0-9]` that the audit-render function passes through unchanged. The other 194 codes (out of 256) are replaced with `#0xNN` escape tokens. The set is frozen at the v1.4.0 release. |
| **numeric escape** | The 5-character sequence `#0xNN` (uppercase hex) that the Number Gate uses to project non-legal byte codes into the audit-render view. A byte like `0x2C` (comma) renders as `#0x2C` in the audit log. |
| **policy-as-data** | The CREC principle: the security policy is data (a `dict`, a `frozenset`), not scattered `if` statements. The runtime, the tests, and the audit doc all read the same data. |
| **Entropy Map** | `docs/entropy-map.md`, the human-readable threat matrix rendered from `ACTION_WHITELIST` and `LEGAL_TEXT_CODES` by the build-time generator. The marker `<!-- generated by scripts/build_entropy_map.py -->` is required. |
| **default-deny** | The CREC default: any action not on the whitelist is rejected. A route registered without a `@requires` decorator fails the DoD. An unknown capability string raises `CapabilityDenied`. |
| **branch** | A fork in a session's message tree. Created by `POST /api/sessions/{sid}/branches` with `parent_message_id` pointing at an earlier message. The new branch's first message is a child of the parent; subsequent appends extend the new branch's tip. |
| **tip** | A message with no children. The user-facing "end" of a branch. The query `WHERE NOT EXISTS(SELECT 1 FROM messages c WHERE c.parent_id = m.id)` yields the tips. A session with N branches has N tips (or 1 in the linear case). |
| **`active_tip_id`** | The `Session.active_tip_id` field. The id of the message that `append_message` extends when no explicit `parent_id` is given. Renamed from the v1.5.0 draft name `active_leaf_id` to avoid overloading "leaf" (a tip is *the* active end; a leaf is any node without children). |
| **`parent_id`** | The new `Message.parent_id: str | None` field (v1.5.0+). The id of the message that the current message extends. `None` for the root of a branch. Defaults to `None`; old session files on disk load with `parent_id=None` filled in by `SessionManager.get()`. |
| **`reconstruct_path`** | The function `reconstruct_path(sid, leaf_id) -> list[Message]` that walks `leaf → parent_id → … → root` and returns the reversed list. Max-depth guard: 256 (raises `BranchDepthExceeded`). |
| **stream pinning** | The rule that an in-flight assistant message's `parent_id` is captured at the start of the stream and pinned regardless of intervening `branch.switch` calls. The server-side `stream_in_progress` flag refuses `branch.switch` with `409 Conflict` while a stream is active. |
| **attachment** | A file attached to a chat message. v1.5.0: text-like MIME (`text/plain`, `text/markdown`, `image/svg+xml`) is inline; binary MIME (`image/png`, `image/jpeg`, `image/gif`, `image/webp`, `audio/mpeg`, `audio/wav`, `application/pdf`) is out-of-line as a v0x04 envelope ref. |
| **inline attachment** | An attachment stored directly in `Message.attachments: list[InlineAttachment]`. The body is a UTF-8 string. Lossy on invalid UTF-8 sequences; the caller-provided bytes are accepted. Limited to text-like MIME per ADR-0020. |
| **out-of-line attachment** | An attachment stored under `~/.dhc/attachments/{session_id}/{uuid}.bin` and wrapped in a v0x04 envelope with name `attachment:{session_id}:{uuid}`. The message carries only the ref. Binary MIME only. |
| **attachment ref** | The string `attachment:{session_id}:{uuid}`. The `session_id` is part of the ref so name binding (ADR-0013) prevents cross-session reuse. The `attachment:` prefix makes the ref invisible to `key_lookup`'s `llm_provider_` filter. |
| **filter domain** | One of four declared byte-flow categories: `audit-text` (62-gate), `asset` (no filter), `provider-bytes` (lossless via `from_codes(strict=False)` if needed), `prompt-text` (no filter, governed by C3+C9+length cap). See ADR-0021. |
| **metadata-only endpoint** | An HTTP `GET` that returns shape metadata (configured, timestamps, hint) without ever exposing the value. The v1.5.1.1 `GET /api/secrets` shape is metadata-only; the v1.5.0 shape returned the full `values` map and has been removed. See ADR-0108. |
| **key hint** | The first 3 characters + `…` + last 4 characters of a stored API key, returned in `GET /api/secrets` for visual confirmation. The hint is not a secret: at 7 visible characters it is too short to reconstruct the value, but it lets a user confirm which key is stored (e.g. `sk-…7890`). Empty when the value is shorter than 7 characters. See ADR-0108. |
| **fail-loud permissions check** | A startup check that verifies the secrets log file is `chmod 600` (owner-only). If the file exists with a permissive mode (e.g. `0o644`), the C1 service logs a warning, marks the service `is_insecure_permissions = True`, and returns `403` for `PUT`/`DELETE` until the user runs `chmod 600` and restarts. The `GET` endpoint still works (read-only). The GitHub CLI lesson: silent fallback to permissive mode is rejected. See ADR-0108. |
| **no-prefill contract** | The v1.5.1.1 invariant that the Settings modal's per-row input is *always* empty, even when the server confirms a key is stored. The placeholder is `"Paste key…"` (new key) or `"Replace key"` (rotation). The eye toggle reveals the typed draft, not the stored value. The dirty check is `draft.trim() != ""` (any non-empty draft is a save intent). See ADR-0108. |
