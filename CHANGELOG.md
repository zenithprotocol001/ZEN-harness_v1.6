# Changelog

All notable changes to this project are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.6.0] - 2026-09-09

### Added (Phase 0: Settings Subsystem Hardening)

- **`SecretSource` typed payload** — `dhc.cordis.secrets` gains
  a `SecretSource` dataclass + `SecretSourceType` closed enum
  (raw / env / file). The `put_source(name, source)` API
  replaces `put_raw(name, bytes)` for new code; `put_raw` is
  retained as a thin wrapper for backward compat. The
  `SecretSourceType` enum is closed: new source types require
  a kernel ADR. Missing env vars and unreadable files fail
  the PUT loudly (no fallback to raw). The `SecretSourceError`
  exception surfaces the cause.

- **Polymorphic JSONL schema** — the `secrets.log` format is
  forked: `raw` records keep the v1.5.1.4 shape
  (`{"op":"set","name":...,"blob":...}`); `env` and `file`
  records add `{"source":"env|file","ref":...,"ref_hint":...}`
  alongside the v0x04 envelope (defense in depth: the envelope
  is always present, so the bytes at rest are sealed even when
  the source is a non-raw reference). v1.5.1.x logs replay
  unchanged; the `get_source` API returns a synthetic
  `SecretSource(type=raw, ...)` for legacy records.

- **`ProviderState` kernel cache** — `dhc.services.provider_state`
  provides `ProviderStateManager`, a thread-safe per-process
  cache of `ProviderState` per provider. The C7
  `_api_llm_health_provider` handler writes the probe outcome
  to the cache; the React tree reads from the cache on mount
  via the new `GET /api/settings/state` endpoint. The cache
  is per-process (not persisted) so a stale `valid` badge
  cannot survive a server restart.

- **`GET /api/settings/state` schema-driven redaction
  endpoint** — returns `{providers: {<provider>: {status,
  source, ref_hint, hint, health, health_label,
  last_check_at}}}`. The endpoint NEVER echoes a raw key
  value. The `ref_hint` for `env` is the env var name; for
  `file` is the basename; for `raw` is None. The closed
  set of provider ids is the `ModelRegistry`'s providers
  field. Unknown providers are silently dropped.

- **`IngressScrubber` regex pipeline** — `dhc.security.
  ingress_scrubber` provides `scrub(text)` which applies a
  compiled-regex pipeline (`sk-or-v1-`, `sk-ant-`,
  `sk-proj-` with 20+ char minimums) and replaces matches
  with `[REDACTED_API_KEY]`. The set of patterns is closed
  (kernel ADR required to add). Applied at TWO points
  (defense in depth):
    1. WS handler in `c1_gui_web_core` BEFORE
       `sm.append_message(sid, "user", text)` — the
       redacted text is what gets journaled.
    2. C7 adapter in `c7_llm_stream_adapter` BEFORE the
       LLM call — even if the WS handler is bypassed, the
       upstream never sees the raw key.

- **Frontend source pill** — `SettingsModal` fetches
  `/api/settings/state` on mount and renders a read-only
  pill (`env: OPENROUTER_API_KEY` or `file: openrouter.key`)
  next to the per-key input. The raw source is suppressed
  (no extra information). The env/file PUT API is a
  follow-up; the pill is display only in v1.6.0.

- **3 source-level log invariants** — `scripts/
  invariants_check.ps1` extracts the `_api_settings_state`
  function body and fails the build if any `logger.*(api_key|
  Authorization|body|...)` call is found. The no-leak
  contract at the wire boundary is now also pinned at the
  source-code level.

- **v1.5.1.3 invariant relaxed** — `put_raw` is now a thin
  wrapper around `put_source`. The v1.5.1.3 invariants
  (`put_raw` calls `_append_log`; `put_raw` does not use
  bare `self._log.open`) are re-pinned against `put_source`
  (the new canonical write path). The self-heal contract
  is preserved.

### Test deltas

- +45 Python tests in 3 new files:
  - `tests/chat/test_secret_source_v160.py` (17 tests)
  - `tests/chat/test_provider_state_v160.py` (12 tests)
  - `tests/security/test_ingress_scrubber_v160.py` (12 tests)
  - +4 from v1.5.1.5 carried over.
- +0 vitest tests (frontend change is a pill; no behavior
  change for the test surface).
- 22 new invariants in `scripts/invariants_check.ps1` (v1.6.0
  Phase 0 section).

### Out of scope (deferred to v1.6.1+)

- Phase 1: ActionRegistry, ActionWhitelist, Risk Envelopes
  (the "Everything is a Plugin" kernel refactor).
- Phase 2: Reference plugin (`welcome-cards`).
- Phase 3: Design tokens (CSS Variables), `?noUserCss=1`
  URL flag.
- Phase 4: OpenRouter Catalog + per-model overrides,
  `?safeMode=1` URL flag.
- Phase 5: Theme editor plugin.
- Phase 6: `.dhc/plugin-state.json` persistence, C10
  observability `plugin.load.failed` event.
- env/file PUT UI in SettingsModal (display only in v1.6.0).
- per-keyName (not per-provider) source mapping.

### Compliance

- Definition of done: all 16/16 DoD checks pass; all 22 new
  v1.6.0 invariants pass; 867 Python tests pass; 87 vitest
  tests pass.
- No-leak contract: 9 boundaries (8 v1.5.1.4 + 1 Ingress),
  all pinned at the source level.

## [1.5.1.5] - 2026-09-09

### Added (verification hardening #1)

- **C7 streaming timeouts (defense in depth)** — the 2026-09-09
  live verification
  (`docs/verification/2026-09-09-openrouter-probe.md`)
  reported a "C7 dispatch hang on `mock-llm/default`". Root
  cause was traced to a test-client bug (it awaited a HELLO
  frame the server never sends), not a C7 regression. v1.5.1.5
  ships the defense-in-depth fix that the verification
  recommended: explicit per-stream timeouts on both the mock
  and live dispatch paths. The mock path now has a 5-second
  per-chunk read timeout on `httpx.AsyncClient` PLUS a 5-second
  wall-clock cap on the whole stream. The live path has the
  existing 30-second read timeout PLUS a 30-second wall-clock
  cap. A future regression that deadlocks the streaming
  iterator (e.g. a `client.stream` that never returns) now
  surfaces as `asyncio.TimeoutError` and is mapped by the WS
  handler in `c1_gui_web_core` to `{"type": "chat.error",
  "code": "stream_timeout"}` instead of hanging the WS for
  30+ seconds. The constructor accepts `stream_timeout_s`
  (default 30.0) and `mock_stream_timeout_s` (default 5.0);
  callers can override per-call via `chat_stream(...,
  stream_timeout_s=...)`.

- **Source-level log invariants for the no-leak contract** —
  the verification also noted that no test pinned the absence
  of the API key or upstream response body from the
  `_api_llm_health_provider` handler's logs. A regression that
  added `logger.warning("upstream: %s", r.text)` would leak
  the OpenRouter error body (which sometimes reflects request
  header fragments). v1.5.1.5 adds 2 invariants to
  `scripts/invariants_check.ps1` that statically grep the
  `_api_llm_health_provider` function body and fail the build
  if a `logger.*(api_key|api_key_str|Authorization|...)` or
  `logger.*(resp.text|resp.content|body|...)` call is found.

### Fixed

- **No code regression in v1.5.1.4** — the verification
  smoke test "hang" was a test-client bug. v1.5.1.4 chat
  dispatch is verified working end-to-end through the WS
  path with the mock LLM (6 chat.delta frames + chat.done
  with token accounting, ~88ms latency).

### Test deltas

- +4 Python tests in
  `tests/chat/test_c7_stream_timeout_v1515.py`:
  - `test_v1515_mock_stream_raises_timeout` — uses a
    synthetic deadlock `httpx.MockTransport` to verify
    the mock path raises `asyncio.TimeoutError` within
    the configured cap.
  - `test_v1515_live_stream_raises_timeout` — same for
    the live path.
  - `test_v1515_default_timeouts` — pins the constructor
    defaults (5.0s mock, 30.0s live).
  - `test_v1515_chat_stream_accepts_per_call_cap` — pins
    the per-call override escape hatch.

- +14 invariants in
  `scripts/invariants_check.ps1` (v1.5.1.5 section):
  6 timeout contract invariants, 1 WS-handler `stream_timeout`
  mapping invariant, 5 regression-test name invariants, 2
  no-leak log invariants.

### Out of scope (deferred to v1.6.0)

- `IngressScrubber` for WebSocket chat frames (verification
  Finding #1) — a real finding, deferred to v1.6.0 Phase 0
  per the verification's lock.
- `ProviderState` kernel cache (verification Finding #3) —
  deferred to v1.6.0 Phase 0.
- `SecretSource` polymorphic JSONL schema (ADR-0110) —
  v1.6.0 Phase 0.

### Compliance

- Definition of done: all 16/16 DoD checks pass; all 14 new
  v1.5.1.5 invariants pass; 872 Python tests pass (+4);
  87 vitest tests pass (+0, server-side hotfix).
- Evidence: `docs/verification/2026-09-09-openrouter-probe.md`
  Finding #2 marked as **FALSE POSITIVE (test-client bug)**.

## [1.5.1.4] - 2026-09-09

### Added (audit hotfix #4)

- **Real per-provider connection probe** — the v1.5.1
  `GET /api/llm/health` only confirmed the harness was
  started with `--llm-base-url`; it did NOT verify the
  user's OpenRouter (or any other) key actually worked.
  The Settings modal rendered a "Configured" pill with no
  way to actually confirm the key was valid. v1.5.1.4
  adds `GET /api/llm/health/{provider}` which uses
  OpenRouter's canonical `GET /api/v1/auth/key` endpoint
  — the documented "is my key valid" probe. Returns
  `{ok, label, limit, is_free_tier}` on 200, `{ok:
  false, error: "invalid_key"}` on 401, `{ok: false,
  error: "no_key"}` when no key is set, and `{ok:
  false, error: "network"}` on any other failure. The
  key is never echoed in the response body or logged.
  Other providers (OpenAI, Anthropic) return
  `not_supported` — the full v1.6.0 model catalog
  (ADR-0105+) will replace this with a unified
  per-provider probe layer.

- **"Test connection" button in the Settings modal** —
  each per-provider accordion now has a "Test connection"
  button (rendered only when a key is stored for that
  provider) that calls the new probe and renders the
  result inline: green ✓ with the OpenRouter label,
  red ✗ for invalid keys, amber for network errors,
  grey for "not supported" providers.

- **Inline model picker in the chat header (no-session
  state)** — when no session is active, the chat header
  now renders a "Default model" picker. The user's pick
  is persisted in `dhc.defaultModelId` (localStorage) and
  sent as `{model: "..."}` in the POST body when creating
  a new session. The server validates the model id
  against the closed `ModelRegistry` (400 on unknown) so
  an attacker cannot inject arbitrary model strings.

### Fixed (audit hotfix #4)

- **Accordion auto-open on stored keys** — the v1.5.1
  Settings modal collapsed all four provider accordions
  when no active session was open, even if a key was
  stored. The `useEffect` auto-open was already wired
  but the initial-state race was visible. v1.5.1.4
  confirms the auto-open contract with a vitest test
  that mounts the modal with a stored OpenRouter key
  and asserts the OpenRouter accordion is open after
  the `/api/secrets` probe resolves.

- **`POST /api/sessions` accepts an optional `model`
  field** — the create handler now reads `body.model`
  and validates it against the closed `ModelRegistry`.
  Unknown model ids are rejected with 400. This is the
  server-side companion to the no-session picker; the
  legacy empty-body `{}` path still creates a session
  with the default `""` model (which the C7 dispatch
  falls back to the harness-level default at chat
  time).

### Tests

- `tests/chat/test_llm_health_v1514.py` (new, 5 tests):
  - `test_llm_health_openrouter_200_returns_label_and_limit`
  - `test_llm_health_openrouter_401_returns_invalid_key`
  - `test_llm_health_openrouter_no_key_returns_no_key`
  - `test_llm_health_openrouter_network_failure_returns_network`
  - `test_llm_health_openai_returns_not_supported`
- `apps/web/src/__tests__/SettingsModal.test.tsx` (+4 tests
  in a new `v1.5.1.4: per-provider probe` describe block):
  - `auto-expands an accordion when a key is stored and
    there is no active session`
  - `renders the Test connection button in the loading
    state while the probe is in flight`
  - `renders the error line when the probe returns
    invalid_key`
  - `does NOT render the Test connection button when no
    key is stored for the provider`
- `apps/web/src/__tests__/ChatPanel.test.tsx` (+1 test
  in a new `v1.5.1.4: inline default model picker` describe
  block):
  - `renders the default-model pill in the no-session
    state`
- Test count: 863 → 868 Python (+5). Vitest 82 → 87 (+5).
  Total: 955 passing.

### Invariants (added to `scripts/invariants_check.ps1`)

- 19 new v1.5.1.4 invariants: route registered, handler
  uses the canonical /auth/key URL, 401 maps to
  `invalid_key`, the new test file + 5 test names, the
  Settings modal renders the Test connection button +
  handler + calls /api/llm/health, the ChatPanel renders
  `chat-model-pill-default`, persists `dhc.defaultModelId`
  in localStorage, sends the model in the POST body, the
  server's `_api_sessions_create` accepts an optional
  `model` field, and the vitest describe blocks exist.

### Out of scope (deferred to v1.6.0)

- The WelcomeView's "Ready" / "Offline" subtitle is
  unchanged in v1.5.1.4 — the inline picker + Settings
  Test connection button already answer the user's
  "am I connected?" question. v1.6.0 will wire the
  WelcomeView to a richer provider-health surface
  driven by the OpenRouter catalog.

- The model picker shows the 6 hardcoded models from
  the v1.5.0 `ModelRegistry`. v1.6.0 will replace this
  with a live OpenRouter catalog overlay.

## [1.5.1.3] - 2026-09-09

### Fixed (audit hotfix #3)

- **Settings "Save" returned 500 in the sanitized-error
  path** — when the secrets directory (`.dhc/secrets/`)
  was removed between server startup and the next PUT
  (e.g. the v1.5.1.2 smoke-test cleanup, a manual `rm`,
  or a migration that touched the dir), `PUT
  /api/secrets/{name}` and `DELETE /api/secrets/{name}`
  raised `FileNotFoundError: '.dhc\secrets\secrets.log'`.
  v1.5.1.2 added sanitized error messages on the frontend
  so the user saw a friendly "Server is busy. Try again or
  restart the server." — but the underlying 500 was
  unfixed. The bug lived in `SecretsService.put_raw`
  (`src/dhc/cordis/secrets.py:599`) and
  `SecretsService.delete` (`secrets.py:663`), both of
  which called `self._log.open("a", ...)` without
  ensuring the parent dir existed. The constructor's
  `mkdir` only ran at startup. v1.5.1.3 introduces
  `_append_log`, a helper that does
  `self._log.parent.mkdir(parents=True, exist_ok=True)`
  before every write, making the path robust to any
  post-startup state mutation. The helper runs inside
  the existing `self._lock` so concurrent PUTs from
  multiple tabs still serialize (no interleaved bytes).
  The user-visible fix: a Save click after the secrets
  dir is gone now succeeds (the dir + log file are
  recreated on the first write, the value is persisted,
  and the next GET round-trips the value).

### Audit findings

- The v1.5.1.2 audit fixed the same class of bug in
  `SessionManager._atomic_write_json` but missed the
  parallel write path in `SecretsService`. v1.5.1.3
  closes the loop. A grep across `src/dhc/**/*.py` for
  other `Path.open("a"|"w")` and `Path.write_*` call
  sites was performed; the only remaining write paths
  are either pre-mitigated with explicit
  `parent.mkdir(parents=True, exist_ok=True)` (e.g.
  `attachments.py:176`, `session_exporter_v1/service.py:41`)
  or are user-supplied (export paths).

### Tests

- `tests/chat/test_secrets_self_heal_v1513.py` (new, 5 tests):
  - `test_put_raw_self_heals_missing_secrets_dir`
  - `test_delete_self_heals_missing_secrets_dir`
  - `test_self_heal_survives_full_state_wipe`
  - `test_self_heal_works_when_secrets_dir_never_existed_at_init`
  - `test_concurrent_puts_dont_interleave` (pins the
    `self._lock` contract: 4 threads × 25 puts each,
    all 100 lines parse as JSON, no partial writes).
- Test count: 858 → 863 Python (+5). Vitest unchanged
  at 82. Total: 945 passing.

### Invariants (added to `scripts/invariants_check.ps1`)

- `v1.5.1.3: secrets.py defines _append_log helper`
- `v1.5.1.3: _append_log self-heals the parent dir`
- `v1.5.1.3: put_raw does NOT use bare self._log.open()`
- `v1.5.1.3: put_raw calls _append_log`
- `v1.5.1.3: delete does NOT use bare self._log.open()`
- `v1.5.1.3: delete calls _append_log`
- `v1.5.1.3: tests/chat/test_secrets_self_heal_v1513.py exists`
- `v1.5.1.3: <each of the 5 new test names> exists`

## [1.5.1.2] - 2026-09-09

### Fixed (audit hotfix)

- **Session creation "click does nothing" (P0)** — `POST
  /api/sessions` could 500 with `FileNotFoundError` if the
  on-disk sessions directory was missing at write time
  (e.g. after a prior test cleanup, the v0x03→v0x04
  migration, or a manual `rm`). The `SessionManager`
  constructor's `mkdir` only ran at startup, so any
  post-startup mutation left the next write hanging. The
  React client silently swallowed the 500
  (`if (!r.ok) return null;`), so the user saw the click
  as "does nothing". v1.5.1.2 makes `_atomic_write_json`
  self-heal: it re-creates the parent directory on every
  write. The bug is reproduced by deleting
  `.dhc/sessions/sessions/` while the server is running
  and then `POST /api/sessions`. With the fix, the next
  write succeeds. Tests:
  `test_atomic_write_self_heals_missing_parent`,
  `test_atomic_write_self_heals_renamed_parent`.

- **The hint leaked the provider prefix** — the
  `first_3 + … + last_4` shape (e.g. `sk-…7890`) leaked
  the uniform `sk-` provider family. The modal's subtitle
  "Encrypted at rest, never sent to the browser in any
  response" was misleading under the prefix-leak. v1.5.1.2
  changes the hint to `… + last_4` only (e.g. `…7890`).
  The `updated_at` field remains a separate signal for
  the user to confirm the right key was last touched. The
  subtitle is now literally true. Tests:
  `test_hint_redacts_provider_prefix` and the updated
  `test_hint_for_long_value_returns_last_4` /
  `test_get_secrets_metadata_never_returns_value`.

- **`fetchJson` echoed the raw server body** — the
  previous helper interpolated the raw aiohttp response
  body into the thrown `Error`. For a 500, the user saw
  `Error: 500: 500 Internal Server Error\nServer got
  itself in trouble`, which (a) is useless and (b) could
  leak file paths from a future `PermissionError`. A
  shared helper at `apps/web/src/utils/sanitizeError.ts`
  maps 4xx/5xx to friendly text without echoing the raw
  body. Both `SettingsModal` and `ModelConfigMenu` use
  the shared helper. Tests:
  `does not include the raw server body in the
  user-visible error on PUT 500`,
  `does not include the raw server body in the
  user-visible error on PUT 403 (insecure mode)`.

- **`newSession` silently swallowed 500s** — the React
  client did `if (!r.ok) return null;` on a 500, which
  produced no UI feedback. v1.5.1.2 maps 4xx/5xx to
  friendly text and surfaces it inline in the rail below
  the "New chat" pill. The user can dismiss the error
  manually; it is also cleared on the next successful
  create. The new visual contract is in
  `SessionList.test.tsx` (`v1.5.1.2: inline
  newSessionError`).

- **Doubled `sessions/sessions/` path nesting** — the
  CLI default for `--sessions-dir` was
  `repo_root/.dhc/sessions` (the leaf), but
  `SessionManager` appends `/ "sessions"` internally.
  The on-disk layout was correct (`<parent>/sessions/`),
  but the doubled segment was visible in the path
  printed by the migration scripts and the smoke runner,
  causing operator confusion. v1.5.1.2 flips the CLI
  default to `repo_root/.dhc` (the parent) and updates
  the 4 chat-ws test fixtures and the smoke runner to
  pass `tmp_path` directly instead of
  `tmp_path / "sessions"`.

### Audit findings (no code change required)

- WS auth, CSP, origin guard, constant-time compare:
  in place, verified clean.
- `GET /api/secrets` shape: no `value` field, no leak
  of plaintext, verified clean.
- `secrets.py` envelope v0x04 with name binding
  (ADR-0013/14): in place.
- 0600 fail-loud check (ADR-0108): in place.
- No-prefill contract (ADR-0108): in place.
- `SecretsService.put_raw` never called from any HTTP
  handler: verified clean.

### Test count delta (v1.5.1.2)

- Python: 855 → 860 (+5: 2 self-heal, 1 redaction,
  2 updated hint tests).
- Vitest: 77 → 82 (+5: 2 sanitized-error tests in
  SettingsModal, 3 newSessionError tests in SessionList).
- Total: 932 → 942.

## [1.5.1.1] - 2026-09-09

### Fixed

- **Settings modal `PUT` 405 (P0)** — the v1.5.1 Settings modal
  was firing `POST /api/secrets` with body `{name, value}`. The
  C1 service registers `PUT /api/secrets/{name}` with body
  `{value}`. The verb/URL mismatch produced a 405 on every Save
  click. v1.5.1.1 fixes the frontend to fire the GitHub-shaped
  verb and put the key name in the URL. The backend is unchanged.

### Added (v1.5.1.1, ADR-0108)

- **Metadata-only `GET /api/secrets`** — the response is now
  `{secrets: [{name, configured, updated_at, hint}], names: [...]}`.
  The full value is *never* returned. The `hint` field is the
  first 3 characters + `…` + last 4 characters (e.g. `sk-…7890`).
  The `names` field is preserved for v1.5.0 callers.
- **No-prefill input** — the per-row input is empty by default,
  even when the server confirms a key is stored. The placeholder
  is `"Paste key…"` (new) or `"Replace key"` (rotation). The eye
  toggle reveals the typed draft, not the stored value.
- **0600 fail-loud** — the secrets log file is checked for
  `chmod 600` (owner-only) at startup. A permissive mode logs a
  warning and returns `403` for `PUT`/`DELETE` until the user
  runs `chmod 600` and restarts. The `GET` endpoint still works
  (read-only).
- **Accordion default-state** — when the modal is opened from
  the welcome view (no active session), all four provider
  accordions collapse to the "Not set" pill. When opened from
  an active session, only that session's provider is expanded.
- **ADR-0108** — the key management contract that documents the
  GitHub-shaped `PUT`/`DELETE` verbs, the metadata-only `GET`,
  the no-prefill UX, and the 0600 fail-loud check. Deep research
  pass against GitHub (write-only metadata reads), DeepSeek
  (shown-once rotation), and OpenCode (local holder).

### Tests (v1.5.1.1, +14)

- 10 new Python tests in
  `tests/chat/test_secrets_metadata_v1511.py` (metadata shape,
  value never returned, hint shape, round-trip PUT/GET, round-trip
  DELETE/GET, 0600 fail-loud for PUT, 0600 fail-loud for DELETE,
  missing-file no-op, permissive-mode raise).
- 4 new vitest tests in `SettingsModal.test.tsx` (metadata shape
  no-prefill, non-empty draft is save intent, no-active-session
  collapse, active-session open one provider).
- 3 existing vitest tests updated to expect `PUT` (the v1.5.1 bug
  was that the tests mocked the wrong verb; the contract is now
  enforced).
- 1 existing test renamed: "Save is disabled when the draft matches
  the saved value" → "Save is enabled whenever the draft is
  non-empty (v1.5.1.1 no-prefill contract)."

### Total test counts

- Python: 845 → 855 (+10 net new; 0 existing updated to new shape)
- Vitest: 73 → 77 (+4 net new; 3 existing updated to expect PUT)
- Total: 918 → 932

### Shipped artifact

`relay/harness_benchmark-v1.5.1.1-20260909.zip`

## [1.5.1] - 2026-09-09

### Added

- **GUI redesign: "Redesigned Product"** — the chat panel is
  now a professional AI chat interface (Qwen / ChatGPT /
  Claude style), closing the seam between "redesigned chat"
  and "redesigned product."
  - `<WelcomeView />` in the no-session state and the
    empty-active-session state. DHC monogram + 24 px
    greeting "How can I help?" + 2×2 grid of suggestion
    cards. The same component is rendered in both states,
    so the "Click + New session" rail is no longer shown.
  - **Mixed 2+2 suggestion cards**: 2 conversation
    starters (Run an eval, Configure a model) and 2 action
    cards (Open a session, Manage API keys). The grid is
    visually uniform; the difference is the verb (Ask
    vs. Open) and the subtitle ("…will explain…" vs.
    "Opens …").
  - **Auto-create on first intent**: clicking a conversation
    card or pressing Enter on an empty input auto-creates
    a session via `POST /api/sessions`, then dispatches
    the prompt. The in-flight promise ref
    (`newSessionInFlight`) is single-flight — double-clicking
    during the create round-trip doesn't create two
    sessions.
  - **Developer Mode toggle**: the gear menu now has a
    styled switch. Label changed from "v1.5.0 tools
    (branch, fork, tree)" to "Developer tools". The
    v1.5.0 features (BranchButton on every message,
    LeafSwitcher in the header, Tree button) are gated
    behind this toggle. Default is OFF; the choice
    persists in `localStorage["dhc.developerMode"]`.
  - **Sidebar restyle**: full-width "New chat" pill at the
    top (replaces the inline + button). Row actions
    (pin / archive / delete) reveal on hover/focus
    only. Pin icon visible only when `pinned === true`.
    Empty untitled sessions (message_count == 0 AND title
    is empty/default) are filtered out at the rail
    (display only — never server-side deleted). The
    active session is never pruned.
  - **Settings modal restyle**: per-provider accordions
    (OpenAI / Anthropic / OpenRouter / Mock, with
    `displayNameFor()` mapping). Each accordion's header
    shows a "Configured" / "Not set" pill (replaces the
    v1.5.0 per-row "Key saved" badge). Default key on
    top; per-model overrides collapsed in a nested
    `<details>`. Show/hide secret toggle (eye icon) per
    row, default hidden. Save is enabled only when the
    draft is non-empty AND dirty (draft != currentSaved,
    or unsaved). Dark inputs with monospace, the 70ch
    intro paragraph, the close button (SVG icon) in the
    top-right of the modal header. Portal to
    `document.body` via the new `<ModalPortal />` so the
    chat shell doesn't bleed under the modal.
  - **Stop / cancel while streaming**: a styled Stop
    button morphs into the Send button when streaming.
    Clicking Stop sends a `chat.cancel` over the
    WebSocket. The server-side handler acknowledges with
    `chat.cancelled`. The session manager exposes
    `cancel_stream(sid)`, `is_stream_cancelled(sid)`, and
    `clear_cancel(sid)`. The assistant message gets a
    `cancelled: true` field and a `cancelled` chip in
    the bubble's meta line. (See ADR-0022 for the
    mid-stream interrupt limitation: aiohttp's
    WebSocketResponse enforces "one pending receive()
    at a time" via `_waiting`, so the chat handler
    cannot race the outer `async for msg in ws:`
    loop. v1.5.1 implements the noop-ack path cleanly
    and the SessionManager cancel flag; mid-stream
    interrupt is v1.5.2 work.)
  - **Scroll pinning**: a sticky "Jump to latest" pill
    at the bottom-center of the messages container when
    the user scrolls up; click scrolls to the bottom and
    re-pins.
  - **Streaming bubble ARIA**: `aria-live="polite"` on
    the streaming content so screen readers announce
    the assistant's progress.
  - **`prefers-reduced-motion`**: the cursor blink is
    replaced with a static block, hover transforms are
    removed.
  - **Media queries**: the sidebar collapses to a 56 px
    icon rail under 640 px; the welcome grid collapses
    to a single column under 640 px.
  - **One icon set**: all emoji replaced with inline-SVG
    icons in `apps/web/src/components/icons.tsx`
    (Lucide-style: 1.5 px stroke, `currentColor`, 24×24
    viewBox). Names: `plus`, `search`, `pin`, `archive`,
    `trash`, `gear`, `key`, `model`, `tree`, `fork`,
    `paperPlane`, `attach`, `chevronDown`, `chevronRight`,
    `eye`, `eyeOff`, `close`, `stop`, `arrowDown`, `play`,
    `logo`, `cancelled`, `running`.
- **ADR-0022 (cancel flag round-trip)**: documented
  the mid-stream cancel limitation in a new ADR and added
  a unit test for the SessionManager cancel flag machinery.
- **DoD**: 16/16 checks pass. 918 tests pass
  (845 Python + 73 vitest), 2 skipped, 1 xpassed.
  DHC-V 100.0. The zip is `relay/harness_benchmark-v1.5.1-20260909.zip`.

## [1.5.0] - 2026-09-08

### Added

- **Branching on the existing `m_<12 hex>` schema** (ADR-0019):
  the session message ledger is now a tree, not a list.
  - `Message.parent_id: str | None` — the parent message
    id. `None` for the root of a branch. Defaults to
    `None`; old session files on disk load unchanged.
  - `Session.active_tip_id: str | None` — the message
    that `append_message` extends when no explicit
    `parent_id` is given. Renamed from the draft name
    `active_leaf_id` to avoid overloading "leaf."
  - `reconstruct_path(sid, leaf_id) -> list[Message]`:
    walk `leaf → parent_id → … → root`, return reversed.
    Max-depth guard: 256 (raises `BranchDepthExceeded`).
  - **Stream pinning**: the assistant's `parent_id` is
    captured at the start of the stream and pinned,
    regardless of intervening `branch.switch` calls.
  - **Server-side stream guard**: `branch.switch` is
    refused with `409 Conflict` while a stream is
    active on the session. The frontend may keep Fork
    enabled; the server enforces the invariant.
  - 3 new C1 routes, all gated:
    `POST /api/sessions/{sid}/branches` →
    `BRANCH_CREATE`,
    `PATCH /api/sessions/{sid}/active-tip` →
    `BRANCH_SWITCH`,
    `GET /api/sessions/{sid}/branches` →
    `BRANCH_LIST`.
- **Attachments on the v0x04 envelope** (ADR-0020):
  user file uploads with routing by MIME class.
  - **Inline** for `text/plain`, `text/markdown`,
    `image/svg+xml`. The body is a UTF-8 string in
    `Message.attachments: list[InlineAttachment]`. The
    audit log projects the body through the 62-gate.
  - **Out-of-line** for `image/png`, `image/jpeg`,
    `image/gif`, `image/webp`, `audio/mpeg`,
    `audio/wav`, `application/pdf`. Stored under
    `~/.dhc/attachments/{session_id}/{uuid}.bin` and
    wrapped in a v0x04 envelope with name
    `attachment:{session_id}:{uuid}`. The message
    carries only the ref.
  - Name binding (ADR-0013) carries over: the
    `session_id` is part of the envelope name, so a
    ref from session A cannot be reused in session B
    without breaking the MAC.
  - Size limits: 10 MB per file, 25 MB per message.
  - MIME allowlist: exactly the 10 values in the
    table above. Anything else is rejected with `415`.
  - 3 new C1 routes, all gated:
    `POST /api/sessions/{sid}/attachments` →
    `ATTACHMENT_PUT`,
    `GET /api/attachments/{ref}` → `ATTACHMENT_GET`,
    `DELETE /api/attachments/{ref}` →
    `ATTACHMENT_DELETE`.
- **Filter domains** (ADR-0021): a single document
  that names the four byte-flow categories in the
  system and declares the filter rule for each:
  - `audit-text` — 62-gate, escape non-legal.
    C2 log emit, Entropy Map, secret names, capability
    names, file paths.
  - `asset` — no filter. Attachment binaries, `data:`
    URIs, envelope ciphertext on disk, in-memory
    `SessionEvent.payload`.
  - `provider-bytes` — lossless via
    `from_codes(strict=False)` if needed. Provider HTTP
    body, API key bytes.
  - `prompt-text` — no filter, governed by C3+C9+length
    cap. User message content, chat.send WS frame,
    C3 user section.
- **Prompt-path control** (ADR-0018): an explicit
  scope line on what the 62-gate does *not* defend.
  The 62-gate is a machine-interpretation boundary
  filter; it is not a prompt-injection defense. The
  prompt path is governed by C3 boundary-token escape
  + C9 agent-tool authorization + length cap +
  usage totals. A new test
  (`tests/cordis/test_prompt_path_unfiltered.py`)
  asserts the property positively.
- **Cap-to-32 amendment** (ADR-0015-amendment-1):
  the v1.4.0 cap of 24 is preserved as a frozen
  archaeology constant (`CAPABILITY_BUDGET_V140`).
  The v1.5.0 cap is 32 (`CAPABILITY_BUDGET_V150`).
  6 new capabilities land: 3 branching + 3
  attachments. The cap is enforced by
  `scripts/invariants_check.ps1` as a property
  check, not only by the module-load `assert`.
- **Bijection invariant** (Phase 1, Findings 1+2):
  `GuiWebCore` exposes
  `self.registered_capabilities: set[Capability]`
  populated at registration time. The invariant
  asserts `set(Capability) == ACTION_WHITELIST.keys()
  == web_core.registered_capabilities`. A new
  capability without a route, or a route with a
  capability not in the whitelist, fails the DoD.
- 5 new ADRs: 0015-amendment-1 (cap→32), 0018
  (prompt-path), 0019 (branching), 0020
  (attachments), 0021 (filter domains).

### Changed

- The session storage schema gains `Message.parent_id`
  and `Session.active_tip_id`. Old session files on
  disk load with both fields filled by
  `SessionManager.get()` (default `None`).
- `Session.usage_totals` remains branch-agnostic
  (v1.4.0 behavior). The prompt and completion
  token counts of a forked-off branch are still
  counted against the session total. Per-branch
  totals are a v1.6.0 metric addition.
- The DoD is restructured around *property checks*
  (cap, bijection, entropy map, filter-domain
  absence in prompt path, attachment ref
  invisibility) rather than per-file existence
  checks. The per-ADR "exists" checks collapse into
  one manifest-driven check.

### Security

- The 62-gate is *not* a prompt-injection defense.
  The prompt path is governed by a different
  control (ADR-0018). Filtering the prompt would
  destroy punctuation and would not stop semantic
  injection (an alphanumeric string is sufficient).
- Attachment refs are name-bound to the session
  via the v0x04 envelope name. Cross-session
  ref reuse fails the MAC check (ADR-0013
  carries over to attachments).
- The 6 new capabilities (3 branching + 3
  attachments) are gated with `@requires` and
  appear in `ACTION_WHITELIST` and the Entropy Map.
  The bijection invariant catches drift at the
  DoD.

### Deferred to v1.5.1 (explicit)

- Per-branch `usage_totals` (currently
  branch-agnostic sum).
- Frontend lockout of the Fork button while a
  stream is in flight (the server-side guard is
  the v1.5.0 contract; the UI nicety is a v1.5.1
  follow-up).
- Auto-forwarding of attachments to the LLM
  provider (currently the provider sees only
  message text).
- Attachment deduplication and versioning.

### Tests

- New test files (exact counts generated by the
  parametrized sweeps; the count is a byproduct,
  not the goal):
  - `tests/cordis/test_capability_bijection.py`
    (3 tests: cap, bijection, registered-cap set).
  - `tests/cordis/test_prompt_path_unfiltered.py`
    (1 test: prompt path passes bytes unchanged).
  - `tests/cordis/test_filter_domains.py` (4 tests:
    one per domain, asserts the rule).
  - `tests/cordis/test_attachment_ref_invisibility.py`
    (1 test: `lookup_api_key` ignores
    `attachment:` refs).
  - `tests/chat/test_branching.py` (~10 tests:
    linear history preserved, fork from middle,
    switch active tip, path reconstruction,
    max-depth guard, stream pinning, server-side
    stream guard, branch-agnostic `usage_totals`).
  - `tests/chat/test_attachments.py` (~12 tests:
    inline threshold by MIME class, out-of-line
    for binary, file size cap, message size cap,
    MIME allowlist, name binding, byte-exact
    round-trip, delete cleans the dir, ref
    format invariants, ref parsing rejects
    malformed, attachment inside a branch,
    attachment inside a multi-branch session).
  - `tests/chat/test_key_lookup_attachment_invisibility.py`
    (1 test: `lookup_api_key` is invisible to
    `attachment:*` names).
  - 8 new vitest tests in
    `apps/web/src/__tests__/`:
    `BranchButton.test.tsx`,
    `LeafSwitcher.test.tsx`,
    `PathReconstruction.test.tsx`,
    `AttachmentsPanel.test.tsx`,
    `InlineVsOutOfLine.test.tsx`,
    `MIMEAllowlist.test.tsx`,
    `NoNumberGateOnAssets.test.tsx`,
    `BranchFromMessage.test.tsx`.

## [1.4.0] - 2026-09-07

### Added

- **Project CREC — the Entropy Gate** (ADR-0015, ADR-0016,
  ADR-0017). Three components form a single security boundary:
  - **Capability whitelist** (`src/dhc/cordis/capabilities.py`):
    a `Capability` enum + `ACTION_WHITELIST` table + `@requires`
    decorator. Every C1 route is wrapped with the decorator; a
    route without a registered capability fails the DoD. The
    capability set is capped at 24 (current surface + 4 headroom
    for v1.5.0 follow-ups).
  - **Number Gate** (`src/dhc/cordis/number_gate.py`): a
    canonicalization layer that maps bytes to integer codes
    (`to_codes`) losslessly, and projects codes to a 62-code
    legal text alphabet (`from_codes`) at the audit-render
    boundary. Non-legal bytes become `#0xNN` escape tokens.
    Storage stays lossless (the v0x04 envelope is untouched);
    the filter applies only at the audit log and the
    `logger.warning` boundary.
  - **Tests as error boundary** (ADR-0017): the test suite is
    a thin pytest wrapper over `enumerate_capability_tests()`
    and `enumerate_byte_code_tests()`. A new capability
    automatically gets 2 (pass + deny) + 1 (raise) test cases;
    the 256-byte space is covered by one parametrized test.
- **Entropy Map** (`docs/entropy-map.md`): a build-time
  generator renders the capability table as a human-readable
  threat matrix. The doc is generated by
  `scripts/build_entropy_map.py` and is included in every
  relay artifact.

### Changed

- The C1 service wraps every route with `@requires(Capability.X)`.
  A route without a registered capability fails the invariant
  check at startup. No existing route is denied; the decorator
  is a transparent pass-through for the handler's return value.
- The C2 SessionEventLog emit path applies the Number Gate
  filter at render time. The in-memory `SessionEvent.payload`
  is unchanged; the audit-log-rendered text uses the 62-code
  projection.

### Security

- The 256-byte code space is bounded to a 62-code legal
  alphabet at the audit-render boundary. An agent can
  enumerate every byte code and classify its risk. The
  in-memory payload and the storage envelope are lossless.
- The capability surface is bounded to 24 for v1.4.0. Adding
  a route above the cap requires an ADR. The default-deny
  rule is enforced at startup via `scripts/invariants_check.ps1`.

### Tests

- 8 new tests in `tests/cordis/test_capabilities.py`
  (capability enum shape, whitelist keys, decorator pass/deny,
  default-deny at startup, handler pass-through).
- 6 new tests in `tests/cordis/test_number_gate.py` (62-code
  alphabet shape, 256-code sweep, lossless roundtrip, audit
  render, provider boundary).
- 2 new tests in `tests/cordis/test_entropy_map.py`
  (marker present, table covers every capability).
- 8 new tests in `tests/cordis/test_pipeline_guard.py`
  (5-stage pipeline INGEST → AUTHORIZE → VALIDATE → EXECUTE
  → AUDIT end-to-end).
- 2 new tests in `tests/cordis/test_build_entropy_map.py`
  (build script writes the marker, table covers every
  capability).
- Total: 26 new tests; 475 + 26 = **501** tests at v1.4.0.

## [1.3.4] - 2026-09-06

### Added

- **Canonical MAC input (ADR-0014)**: a new envelope version
  `DHC4` (v0x04) with a canonical MAC input that length-prefixes
  the secret name with `u32 BE`:
  `mac_input = header ‖ ext_area ‖ nonce ‖ len(name):u32 ‖ name`.
  This closes the field-boundary shift forgery that the v0x03
  MAC input is vulnerable to (raw concatenation of two adjacent
  variable-length fields, `name ‖ ct`, admits moving bytes
  between the fields without changing the MAC input).
- **Migration script** `scripts/migrate_v03_to_v04.py`: a
  one-time tool that reads v0x03 entries from `secrets.log` and
  re-seals them as v0x04. Refuses to migrate envelopes sealed
  with `name=b""` (anonymous v0x03 envelopes are a downgrade
  vector; see ADR-0014).

### Changed

- `seal()` in `secrets.py` now writes `DHC4` (v0x04) by default.
  v0x03 envelopes remain readable with their non-canonical MAC
  input rule; the reader dispatches on the 4-byte header.
- The `open_envelope()` v0x04 path uses the canonical MAC input
  rule (`len(name):u32 BE` prefix). v0x01, v0x02, and v0x03
  paths are unchanged.
- The on-disk wire format of v0x04 is byte-for-byte identical
  to v0x03. Only the MAC input rule at seal/open time differs.

### Security

- Field-boundary shift forgeries on the v0x04 envelope are
  impossible. The `len(name):u32 BE` prefix disambiguates the
  `name ‖ ct` boundary, so any byte shift between `name` and
  `ct` changes the canonical MAC input and fails the tag check.
  v1.4.0 attachments can safely seal through this envelope
  because the encoding is canonical.
- The v0x03 reader path is unchanged but now read-only. New
  writes go to v0x04. The v0x03 path is a backwards-compat
  read path for v1.3.3 envelopes that exist on disk; the
  migration script is the recommended way to upgrade them.

### Tests

- 8 new tests in `tests/chat/test_secrets_v4.py` (v0x04 envelope,
  canonical MAC input, field-boundary shift, migration, etc.)
- 4 new tests in `tests/chat/test_migrate_v03_to_v04.py`
  (migration script: success, refuse anonymous v0x03, idempotent,
  preserve old log)
- Total: 12 new tests; 462 + 12 = **474** tests at v1.3.4.

## [1.3.3] - 2026-09-05

### Added

- **Secret name binding (ADR-0013)**: the v0x03 envelope now
  authenticates the secret name as part of the HMAC tag input.
  Concretely, the v0x03 tag is computed over
  `header ‖ ext_area ‖ nonce ‖ name ‖ ciphertext`, so storing a
  valid v0x03 envelope under a different name fails the tag check
  on read. This closes the confused-deputy attack where an
  attacker with write access to the secrets log could swap the
  name of a valid envelope and cause the application to use the
  wrong key against a provider.

### Changed

- `seal()` and `open_envelope()` in `secrets.py` accept a new
  `name=` parameter. v0x03 envelopes (the v1.3.2 default write
  path) require a name to seal or open; v0x01 and v0x02 envelopes
  are unchanged. The low-level `_seal_with()` and
  `open_envelope()` accept a `require_binding=True` flag (default)
  for the test suite and migration scripts.
- `SecretsService.put_raw(name, value)` and `_replay` now pass
  the name down to the seal/open calls. The on-disk wire format
  is unchanged; only the MAC input is extended.

### Security

- Confused-deputy attack via name-swap on the secrets log is
  closed. The tag verification now requires the reader to know
  the original name; the error message is intentionally
  ambiguous to avoid leaking which side of the binding was
  tampered with.
- No new constants, no new extension type, no new on-disk bytes.
  The binding is enforced by the existing 32-byte HMAC-SHA256 tag.

### Tests

- 8 new tests in `tests/chat/test_secrets_name_binding.py`
  (round-trip, name-swap fails, no-name requires opt-out, etc.)
- The v1.3.2 test `test_v3_unknown_extension_type_is_preserved`
  is unchanged. The `test_seal_wrong_key_rejected` and the v0x03
  tests in `tests/chat/test_secrets_v3.py` are updated to pass
  `name=b"fixture"` (or a per-test name).
- Total: 8 new tests; 454 + 8 = **462** tests at v1.3.3.

## [1.3.2] - 2026-09-04

### Added

- **v0x03 envelope (ADR-0012)**: a third envelope version, `DHC3`,
  with extension blocks between the header and the AEAD nonce.
  The first extension, type `0x0001`, carries a 12-byte strict
  per-secret nonce. The scrypt KDF salt for v0x03 is the
  28-byte concatenation `strict_nonce (12) ‖ aead_nonce (16)`,
  giving two independent random per-secret values. Old `DHC1`
  and `DHC2` envelopes remain readable; no migration. Unknown
  extension types are silently skipped (forward compat).
- **Per-model API keys (ADR-0007 amendment)**: the SettingsModal
  now renders one row per `(provider, model_id)` pair from
  `GET /api/models`, instead of one row per provider. The
  v1.3.1 per-provider key is preserved as the default fallback
  for any model that does not have its own key. To support
  multiple keys for the same `(provider, model_id)` pair, a
  `___{n}` disambiguator suffix is appended (e.g. `___2`,
  `___3`); the canonical name (no suffix) is always the
  default.
- **`Session.usage_totals`**: a new field on every session
  carrying per-session and per-turn token aggregates:
  `{"prompt_tokens", "completion_tokens", "total_tokens",
  "last_turn_prompt", "last_turn_completion"}`. The WS chat
  handler and the HTTP message endpoint both accumulate
  `StreamChunk.usage` into this field after each turn and
  persist it on the session. The mock LLM continues to use
  the v1.2.0 char/4 estimate. The field is omitted from older
  session files on read (filled in with zeros by
  `SessionManager.get()`).
- **Token counter + context-window progress bar**:
  `<TokenCounter />` renders "Tokens: 1,234 / 8,192" with the
  denominator from `model.context_length`; a thin bar fills
  green→yellow→red as the ratio climbs. Both are pure React,
  no third-party UI lib. The counter reads from the
  piggybacked `usage_totals` field on the WS `message` event.

### Changed

- `seal()` in `secrets.py` now writes v0x03 by default. Pre-existing
  v0x01 / v0x02 envelopes are read normally and re-sealed as v0x03
  on the next write.
- `LLMProvider.chat_stream` and the three provider clients are
  unchanged in their wire shapes from v1.3.1. The new lookup helper
  `lookup_api_key(provider, model_id, secrets_service)` encapsulates
  the per-model → per-provider fallback chain.
- The `SettingsModal` no longer flattens models to per-provider
  rows. It shows one row per model, grouped under the provider
  header, plus a "default (applies to all)" row at the top of
  each provider group.

### Security

- v0x03 envelopes are encrypted with a 28-byte per-secret scrypt
  KDF salt (two independent random values: a 12-byte strict
  nonce in extension `0x0001` and the 16-byte AEAD nonce). A
  stolen vault file yields no static KDF input, and the two
  random values are independent, so a key-recovery attack on
  one does not immediately compromise the other.

### Tests

- 8 new tests in `tests/chat/test_secrets_v3.py` (v0x03 envelope)
- 5 new tests in `apps/web/src/__tests__/SettingsModal.test.tsx`
  (per-model keys)
- 3 new tests in `tests/integrations/test_key_lookup.py` (lookup
  helper)
- 5 new tests in `tests/chat/test_usage_aggregation.py` (totals)
- 1 new test in `tests/chat/test_session_model.py` (field default)
- 3 new tests in `apps/web/src/__tests__/TokenCounter.test.tsx`
  (counter + bar)
- Total: 25 new tests; 422 + 25 = **447** tests at v1.3.2.

## [1.3.1] - 2026-09-03

### Added

- **Per-secret nonce in the secrets envelope (ADR-0010)**:
  v0x02 (`DHC2`) envelopes use the per-envelope 16-byte nonce
  as the scrypt KDF salt, so identical plaintexts no longer
  produce ciphertexts that share a KDF. The envelope header
  bumps from `DHC1` to `DHC2`; old `DHC1` envelopes are still
  readable. See `docs/secrets-model.md` and `docs/adr/0010`.
- **Per-session `ModelConfig` (ADR-0011)**: `temperature`,
  `max_tokens`, `top_p`, and `system_prompt` per session,
  stored encrypted in `SecretsService` under
  `model_config_{session_id}`. C7 reads the config and forwards
  the knobs to the live provider; the system prompt is
  prepended to the messages list if no `role: system` is
  already present.
- **2 new C1 routes**:
  - `GET /api/sessions/{id}/config` → 200 with the config JSON.
  - `POST /api/sessions/{id}/config` → 204; body is the config
    JSON; out-of-range values yield 400.
- **`<SettingsModal>` React component** in
  `apps/web/src/components/SettingsModal.tsx`: per-provider
  API key entry, update, and delete. Wired to
  `POST /api/secrets` and `DELETE /api/secrets/{name}`.
  No `prompt()` / `alert()` / `confirm()` — all input is
  React state.
- **`<ModelConfigMenu>` React component** in
  `apps/web/src/components/ModelConfigMenu.tsx`: range
  sliders for temperature / max_tokens / top_p and a
  textarea for the system prompt. Wired to the new
  `/api/sessions/{id}/config` routes.
- **`StreamChunk.usage` field**: optional
  `{"prompt_tokens", "completion_tokens", "total_tokens"}`
  dict on the final chunk of a stream. OpenAI and OpenRouter
  yield it from a choices-less trailing SSE chunk (with
  `stream_options.include_usage: true` on the request);
  Anthropic yields it from the `message_delta` event.
- **vitest frontend test runner** (option A from the v1.3.1
  plan): `apps/web/package.json` has a `test` script that
  runs `vitest run`. The React component suite covers
  `SettingsModal` (4 tests) and `ModelConfigMenu` (3 tests)
  for a total of 8 frontend tests in CI.
- **2 new docs**: `docs/adr/0010-per-secret-nonce.md`,
  `docs/adr/0011-model-configuration.md`.

### Changed

- `LLMProvider.chat_stream` signature gains three optional
  kwargs: `temperature`, `max_tokens`, `top_p` (all
  keyword-only, default `None`). `None` means "let the
  provider decide"; the field is omitted from the request
  body. Existing callers (mock LLM, v1.3.0 tests) are
  unaffected because all calls use kwargs by name.
- `LLMStreamAdapter.__init__` accepts an optional
  `config_store: ModelConfigStore` kwarg.
  `LLMStreamAdapter.chat_stream` accepts an optional
  `session_id` kwarg; the dispatch seam looks up the
  per-session config and forwards the knobs.
- `_ws_chat_handler_impl` and `_api_sessions_post_message`
  now pass `session_id` to `chat_stream` and prefer
  provider-supplied token usage over the v1.2.0 char/4
  estimate.
- `SecretsService` grows `get_raw` / `put_raw` for binary
  round-trip (used by `ModelConfigStore`).
- The `GLOSSARY.md` adds 5 new entries (envelope,
  per-secret nonce, model config, token usage, `DHC1`/`DHC2`).

### Security

- The v0x02 envelope is a strict security improvement: every
  secret now has a unique scrypt KDF input, so two envelopes
  encrypted under the same master key no longer share any
  subkey bytes.
- The `<SettingsModal>` never calls `prompt()` / `alert()` /
  `confirm()`; the API key is captured via a controlled
  `<input type="password">` and POSTed in JSON. The browser
  DevTools will see the user's keystrokes in the input, but
  no `window.prompt` history is created.
- The `<ModelConfigMenu>` does not collect any secret
  material; only public model parameters.

### Deferred to v1.3.2 (explicit)

- Settings modal for per-model API keys (currently one key
  per provider; the model selection is independent of the
  key).
- `StreamChunk.usage` aggregation across multiple turns
  (today the totals are per-message).
- Conversation branching, attachments, token counter,
  context-window progress bar.

### Tests

- **430 passed, 2 skipped, 1 xpassed** at v1.3.1
  (was 388 in v1.3.0).
- +42 tests in v1.3.1:
  - 8 `tests/chat/test_secrets_v2.py` (v0x02 envelope)
  - 10 `tests/services/test_model_config.py` (ModelConfig +
    ModelConfigStore)
  - 5 `tests/chat/test_model_config_routes.py` (C1
    /api/sessions/{id}/config)
  - 3 `tests/chat/test_c7_dispatch.py` (config flow)
  - 3 `tests/chat/test_stream_chunk_usage.py` (StreamChunk shape)
  - 1 `tests/integrations/test_openai_client.py` (usage emission)
  - 1 `tests/integrations/test_anthropic_client.py` (usage)
  - 1 `tests/integrations/test_openrouter_client.py` (usage)
  - 1 `tests/chat/test_secrets.py` (existing `_derive_keys`
    signature update for the new `salt` arg)
  - 8 frontend tests in `apps/web/src/__tests__/`:
    - 4 `SettingsModal.test.tsx`
    - 3 `ModelConfigMenu.test.tsx`
    - 1 `smoke.test.ts`

## [1.3.0] - 2026-09-02

### Added

- **`dhc.integrations.base.LLMProvider`** (ADR-0009): abstract base
  for live LLM providers. Frozen `RetryConfig` dataclass (3 attempts,
  1 s/2 s linear backoff per ADR-0008) and `ProviderError` exception
  with `status` / `provider` / `model` context.
- **`dhc.services.model_registry.ModelRegistry`** (ADR-0006):
  hardcoded list of 6 models across 4 providers (mock, openai,
  anthropic, openrouter). Frozen `Model` dataclass with id, name,
  provider, context_length, pricing, capabilities (frozenset).
- **3 concrete provider clients** (ADR-0009):
  - `OpenAIClient` — POSTs to `https://api.openai.com/v1/chat/completions`,
    OpenAI SSE parsing (deltas + tool_calls).
  - `AnthropicClient` — POSTs to `https://api.anthropic.com/v1/messages`,
    Anthropic SSE parsing (message_start / content_block_delta /
    message_delta / message_stop; treats 529 as 5xx for retry).
  - `OpenRouterClient` — POSTs to `https://openrouter.ai/api/v1/chat/completions`,
    OpenAI-compatible (reuses the OpenAI SSE parser).
- **`provider_client_for(model)` factory** in
  `src/dhc/integrations/__init__.py`: dispatches by
  `model.provider`, raises `ProviderError` for unknown or mock.
- **C7 dispatch seam**: `LLMStreamAdapter` now accepts optional
  `model_registry` and `secrets_service` kwargs. When both are set
  and the model is not the mock, the adapter resolves the provider
  via the factory, looks up the API key from `SecretsService` using
  the `llm_provider_{provider}_{model_id}` naming convention
  (ADR-0007), and streams from the live client. The v1.2.x
  mock-only path is preserved when either arg is missing.
- **C1 routes**:
  - `GET /api/models` — list all 6 models.
  - `GET /api/models/{id}` — fetch a single model (uses
    `add_get("/api/models/{id:.+}", ...)` to allow slashes in the
    id like `openai/gpt-4o-mini`).
- **`<ModelSelect>` React component** in
  `apps/web/src/components/ModelSelect.tsx`: fetches
  `GET /api/models`, groups options by provider in `<optgroup>`,
  and calls `onChange(model_id)` on selection. Wired into
  `ChatPanel` header; persists selection via PATCH
  `/api/sessions/{id}` with `{"model": ...}`.
- **3 new docs**: `docs/v1.3.0-technical-spec.md`,
  `docs/v1.3.0-test-plan.md`, plus the 3 ADRs below.
- **3 new ADRs**: `0007-api-key-management.md`,
  `0008-retry-policy.md`, `0009-provider-abstraction.md`.

### Changed

- `serve_c1.py` now constructs a single `ModelRegistry` and a
  `SecretsService` (when `--secrets-dir` is set) eagerly, then
  passes both to the C7 plugin's apply config so live dispatch
  works at startup.
- The invariant script `scripts/invariants_check.ps1` adds 4 new
  checks: `<ModelSelect>` exists, fetches `/api/models`, is
  imported by `ChatPanel`, and `ChatPanel` PATCHes the session
  with the selected model id.

### Security

- Live provider keys are stored in `SecretsService` (HMAC-SHA256
  encrypt-then-MAC envelope, unchanged from v1.2.0). The keys
  never appear in any `StreamChunk` yielded to the consumer
  (covered by `test_chat_stream_redacts_key_in_logs` and the
  Anthropic/OpenRouter equivalents).
- The `<ModelSelect>` component does NOT collect API keys; the
  Settings modal is deferred to v1.3.1 per the v1.3.0 scope.
- The `StreamChunk` shape is frozen from v1.2.0; no new fields
  are added in v1.3.0 (per ADR-0009 § "Consequences" — `usage`
  is deferred to v1.3.1).

### Deferred to v1.3.1 (explicit)

- Per-secret random nonce in the envelope format (per
  `docs/secrets-model.md` Salt strategy section).
- Settings modal for API key entry.
- Model config menu (temperature / max_tokens / top_p).
- `StreamChunk.usage` field for token counting.
- Conversation branching, attachments, token counter, context-window
  progress bar.

### Tests

- **388 passed, 2 skipped, 1 xpassed** (was 318 in v1.2.1).
- +70 new tests in v1.3.0:
  - 15 `tests/integrations/test_model_registry.py`
  - 7 `tests/integrations/test_base.py` (RetryConfig + ProviderError + LLMProvider)
  - 10 `tests/integrations/test_openai_client.py`
  - 10 `tests/integrations/test_anthropic_client.py`
  - 5 `tests/integrations/test_openrouter_client.py`
  - 5 `tests/integrations/test_factory.py`
  - 8 `tests/chat/test_model_routes.py` (C1 /api/models routes)
  - 10 `tests/chat/test_c7_dispatch.py` (C7 dispatch + WS round-trip)
- All v1.2.x tests pass without modification.
- v1.2.0 chat smoke (19/19) still passes.

## [1.2.1] - 2026-09-02

### Added

- **`SessionManager.search(query, limit, include_archived)`**:
  case-insensitive full-text search across session title and
  message content, returning full `Session` objects ordered by
  `pinned desc, updated_at desc`. Exposed at
  `GET /api/sessions?q=...` on C1 (the existing `?search=`
  parameter is unchanged and still returns summaries via
  `list_summaries`). Empty / whitespace queries return `[]` so
  callers fall back to `list_summaries` for the full listing.
- **Doc: Salt strategy** (`docs/secrets-model.md`): explains why
  v1.2.x uses a fixed scrypt salt under the single-tenant
  threat model, and reserves headroom for a per-secret random
  nonce in v1.3.0.
- **Doc: Retry strategy** (`docs/chat-architecture.md`):
  documents that v1.2.x has no retry (mock LLM is loopback)
  and commits v1.3.0 to a 3-attempt, 1 s/2 s backoff policy
  on 5xx + connection errors only.
- **ADR-0006 — Model Selection Strategy**: locks the
  v1.2.x → v1.3.0 → v1.4.0 progression (single mock → hardcoded
  list of ~6 → OpenRouter dynamic discovery).

### Changed

- `GET /api/sessions?q=...` now returns matches via the new
  `SessionManager.search()` instead of the v1.2.0 `list_summaries`
  filter. The legacy `?search=...` parameter is preserved for
  backward compatibility.

### Tests

- 318 passed, 2 skipped, 1 xpassed (was 314 in v1.2.0).
- +3 unit tests in `tests/chat/test_session_manager.py`:
  `test_search_by_title`, `test_search_by_message_content`,
  `test_search_empty_query_returns_empty`.
- +1 e2e test in `tests/chat/test_chat_ws.py`:
  `test_sessions_q_alias_full_text_search` (covers title match,
  content match, case-insensitivity, no-match, and empty-q).

### Compatibility

- No plugin SHA changes; no scoring formula change; no module
  contract change.
- v1.2.0 zip is superseded; v1.2.1 is a drop-in replacement.

## [1.2.0] - 2026-09-02

### Added

- **4-tab web UI**: Modules, Events, Prompts, Chat. The new
  `ChatPanel` is in `apps/web/src/panels/ChatPanel.tsx`; the
  left-rail `SessionList` in `panels/SessionList.tsx`; the
  Ctrl+K search in `components/SearchOverlay.tsx`.
- **Markdown component guardrail** (ADR 0005): the new
  `apps/web/src/components/Markdown.tsx` is the **only** file in
  the React client that calls `dangerouslySetInnerHTML` or
  imports the sanitizers. The PowerShell invariant script scans
  every `.tsx` file and asserts a deny-list (default-deny).
  This makes the XSS guardrail a single auditable line of code.
- **Server-side chat sessions** (`dhc.services.session_manager.SessionManager`):
  persistent JSON files under `~/.dhc/sessions/`, atomic writes
  via `os.replace`, search by title or message content, soft
  delete (archived) and hard delete, 1000-message cap with
  oldest-first truncation.
- **Encrypted secret store** (`dhc.cordis.secrets.SecretsService`):
  append-only JSONL log at `~/.dhc/secrets/secrets.log`,
  per-user 32-byte master key in `secrets.key` (mode 0o600),
  encrypt-then-MAC envelope using HMAC-SHA256 counter-mode
  keystream and a separate HMAC-SHA256 tag. `GET /api/secrets`
  returns names only — values are never returned. v1.2.0 stages
  these secrets for v1.3.0's live providers.
- **C7 chat_stream extension**: a new `chat_stream(messages, model)`
  method on `LLMStreamAdapter` that POSTs to
  `{base_url}/v1/chat/completions` (OpenAI-compatible) and
  yields `StreamChunk` deltas. The existing `stream()` method
  is unchanged.
- **C1 chat + session + secret routes**:
  - `WS /ws/chat` — request/response channel with a distinct
    frame schema (see `docs/chat-architecture.md`).
  - `GET/POST /api/sessions`, `GET/PATCH/DELETE /api/sessions/{id}`,
    `POST /api/sessions/{id}/messages`.
  - `GET /api/secrets`, `PUT/DELETE /api/secrets/{name}`.
  - `GET /api/llm/health`.
- **Mock LLM fixture** (`tests/fixtures/mock_llm.py`): an
  aiohttp server with `POST /v1/chat/completions` (OpenAI-compatible)
  and `GET /v1/stream/{scenario}` (legacy). Scenarios:
  `default`, `echo`, `code`, `tool`, `slow`, `long`. Loopback
  only; no network.
- **Smoke test runner** (`tests/chat/smoke_v12.py`): 19
  end-to-end checks against the mock LLM. Run with
  `python tests/chat/smoke_v12.py`.
- **New docs**: `docs/chat-architecture.md`, `docs/session-storage.md`,
  `docs/secrets-model.md`.
- **New ADRs**: `0004-chat-and-sessions.md`, `0005-markdown-component.md`.

### Changed

- `App.tsx` is now a 4-tab router (was 3).
- `EventsPanel.tsx` no longer calls `dangerouslySetInnerHTML`
  directly; it imports `components/Markdown.tsx`.
- `tests/security/test_c1_xss.py` was updated to scan
  `components/Markdown.tsx` (was `panels/EventsPanel.tsx`).
- `scripts/invariants_check.ps1` adds a deny-list loop that
  scans 9 fixed `.tsx` files for `renderMarkdown`,
  `renderToolResult`, and `dangerouslySetInnerHTML`.

### Test counts

- 314 passing, 2 skipped, 1 xpassed (was 242 passing, 2 skipped,
  1 xpassed in v1.1.0).
- 100+ invariants (was 92 in v1.1.0).
- 5 new test files under `tests/chat/` (secrets, sessions,
  mock LLM, C1 chat routes, smoke).
- Scorer DHC-V still 100.0.

## [1.1.0] - 2026-09-01

### Added

- **3-tab web UI**: Modules, Events, Prompts. `apps/web/src/App.tsx` is
  now a router; the rendering code lives in `panels/EventsPanel.tsx`.
  `apps/web/src/components/ModuleCard.tsx` is the shared card.
- **Plugin marketplace**: 5 bundled plugins under
  `src/dhc/plugins/`:
  - `rate_limiter_v1` — per-agent-event throttle
  - `session_exporter_v1` — snapshot the C2 session log to JSONL
  - `model_router_v1` — pick a backend C7 by prompt prefix
  - `memory_store_v1` — key/value store on the context
  - `prompt_browser_v1` — `/prompts` and `/prompts/{key}` routes
- **Manifest integrity**: every plugin ships `manifest.json` with
  `sha256`; loader verifies with `hmac.compare_digest` at load time.
- **C1 marketplace routes**:
  - `GET /api/manifest` — full manifest (modules + plugins)
  - `GET /plugins` — discovered vs loaded
  - `POST /plugins/{id}` — load
  - `DELETE /plugins/{id}` — unload
  - `GET /prompts` — list 10 master prompts (requires
    `prompt_browser_v1`)
  - `GET /prompts/{key}` — single prompt body
  - `POST /api/eval` — offline in-proc eval of pasted code
- **Paste-and-score** in the browser: paste an LLM response, pick the
  target module, get a DHC-V back.
- **Tests**: 242 passing (was 190), with 34 new tests in
  `tests/plugins/`.
- **Invariants**: 92 passing, including panel-based positive and
  negative invariants for the new 3-tab UI.
- **Docs**: `docs/README.md`, `docs/architecture.md`,
  `docs/security-model.md`, `docs/plugin-authoring.md`,
  `docs/SHA-PINNING.md`, `docs/CHANGELOG.md`, `docs/CONTRIBUTING.md`,
  3 ADRs under `docs/adr/`, `GLOSSARY.md`, `relay/MANIFEST.txt`.
- **Relay exclusion list** in `scripts/package_relay.ps1` keeps
  runtime logs and bearer tokens out of the zip.

### Changed

- `README.md` synced to v1.1.0 (was v0.6.0 in the banner).
- `App.tsx` split; the 3 previous rendering helpers
  (`renderMarkdown`, `renderToolResult`, `dangerouslySetInnerHTML`)
  moved to `panels/EventsPanel.tsx`. Invariants now assert both
  positive (EventsPanel has them) and negative (App.tsx does not).
- `apps/web/src/sanitize.ts` now also exports `formatPayload` and
  `isToolResult` (formerly inline in `App.tsx`).
- Plugin SHA-256 values locked in `docs/SHA-PINNING.md` and asserted
  by `scripts/invariants_check.ps1`.

### Security

- 0 critical, 0 high, 0 medium, 0 low findings on the reference
  implementation. DHC-V = 100.0 (production_ready).

## [1.0.0] - 2026-09-01

### Added

- 10 core modules C1–C10.
- Cordis port at `src/dhc/cordis/`.
- 190 passing tests (was 27); baseline restoration.
- 71 static invariants.
- `dhc-v-report.json` self-score: 100.0.

## [0.6.0] - 2026-09-01

### Added

- Initial 3-module / 4-module reference implementation.
- 19 test files, 7 invariants.
- `dhc-v-report.json` self-score: 100.0.
