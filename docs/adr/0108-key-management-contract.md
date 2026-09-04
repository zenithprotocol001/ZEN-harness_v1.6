# ADR-0108 — Key Management Contract (v1.5.1.1 amendment)

- **Status**: Accepted (2026-09-09); amends ADR-0007
- **Deciders**: harness maintainers
- **Date**: 2026-09-09
- **Supersedes**: (none; amends the surface area of ADR-0007)
- **Related**: ADR-0007 (API Key Management), ADR-0010 (per-secret nonce), ADR-0014 (canonical MAC input)

## Context

ADR-0007 (v1.3.0 → v1.3.2) defined the storage layout and naming convention
for API keys (`llm_provider_{provider}_{model_id}`, optional `___n` suffix)
and chose `SecretsService` (HMAC-SHA256 envelope) as the single source of
truth. The C1 surface was `GET /api/secrets` (returns `{names}`),
`PUT /api/secrets/{name}` (body `{value}`), and
`DELETE /api/secrets/{name}`.

v1.5.1 shipped the redesigned Settings modal. Two design questions arose
that the v1.3.x ADRs did not address:

1. **The metadata-only read contract.** v1.5.1's modal renders a
   "Configured" / "Not set" pill and a per-row show/hide eye toggle.
   The modal also includes the API key value in its in-memory state
   so that the dirty gate can compare the draft against the saved
   value. This is *technically* a re-display of the user's own key,
   which is fine in DHC's threat quadrant (the user holds their own
   keys locally — `OpenCode`'s quadrant), but it conflicts with the
   GitHub-shaped contract: GitHub's `GET` on a secret returns metadata
   only (`name`, `created_at`, `updated_at`, visibility), never the
   value. We need to formalize which side of the line DHC stands on.

2. **The `POST` vs `PUT` 405 bug.** v1.5.1's `SettingsModal` was firing
   `POST /api/secrets` with body `{name, value}`. The C1 service
   registers `PUT /api/secrets/{name}` with body `{value}`. The result
   is a 405. The 405 surfaced as a P0 bug on the v1.5.1 ship because
   the user could not save keys. The fix is to converge on the
   GitHub-shaped verb: `PUT /api/secrets/{name}` with body `{value}`.

This ADR also draws on a deep research pass against GitHub, DeepSeek,
and OpenCode's secret-management surfaces (see ship log). The
synthesis: DHC is in OpenCode's quadrant (the user holds their own
keys locally) and the GitHub-shaped contract for *metadata reads* is
correct discipline; the OpenCode/DeepSeek-shaped *re-display* of own
keys is correct in this quadrant. We therefore adopt the GitHub
metadata-only GET and the OpenCode never-prefill write UX, with the
explicit understanding that the server *never* sends the value back
in any response.

## Decision

The v1.5.1.1 hotfix implements the GitHub-shaped contract for
`/api/secrets`:

### 1. `GET /api/secrets` returns metadata only

```
{
  "secrets": [
    { "name": "llm_provider_openai_gpt-4o-mini",
      "configured": true,
      "updated_at": 1757430912.345,
      "hint": "sk-…1234" },
    ...
  ],
  "names": ["llm_provider_openai_gpt-4o-mini", ...]
}
```

- `hint` is the first 3 characters + `…` + last 4 characters of the
  stored value, or empty when shorter than 7 characters. The hint is
  *not* a secret — it is too short to reconstruct the value but long
  enough for the user to confirm which key is stored (the GitHub-style
  "redaction by truncation" pattern, adapted for re-display).
- The full value is **never** returned in any response, in any form.
  Property-based test: 50 random `PUT` + `GET` round-trips; assert no
  `sk-…`-shaped substring longer than 8 characters appears in any
  response (this is a strict superset of "the value is never sent").
- `names` is preserved for backwards compat with the v1.5.0 callers
  that consume the legacy shape. New code consumes `secrets`.

### 2. The input is *never* prefilled

The modal's per-row input is empty by default, even when the server
confirms a key is stored. The placeholder is:

- `"Paste key…"` when the row has no stored value.
- `"Replace key"` when the row has a stored value (signaling rotation
  intent).

This is the OpenCode/DeepSeek quadrant: the user is the *owner* of
their keys, but the surface area for accidental disclosure (browser
extensions, screenshots, screen sharing) is reduced by never
auto-filling. The show/hide eye toggle is still available, but it
shows the *typed* draft, not the stored value (because we never have
the stored value in the browser).

### 3. The dirty gate is "non-empty draft" (not "draft != saved value")

Because the saved value is never returned, we cannot compare the draft
against it. The dirty check becomes simply `r.draft.trim() !== ""`.
This is a behavior change from v1.5.1 (where the dirty check could
also short-circuit on `draft == savedValue`) and is documented in
`SettingsModal.test.tsx` as "v1.5.1.1 no-prefill contract."

### 4. The hint is not a substitute for the value

The hint is for the user to confirm which key is stored. It is *not*
intended to be used as input to "remember" the key — the user must
always paste a fresh key to update. The provider rotation flow
("replace key") is therefore an explicit user action with no
shortcut.

### 5. The verb shape is GitHub-style

- `GET /api/secrets` — metadata only.
- `PUT /api/secrets/{name}` body `{value}` — upsert. 201 on create,
  204 on update.
- `DELETE /api/secrets/{name}` — remove. 204.

The v1.5.1.1 frontend no longer fires `POST /api/secrets` (the
v1.5.1 bug). The v1.5.0 backend route is unchanged; the v1.5.1
SettingsModal was the side that had drifted from the contract.

### 6. The 0600 file-mode check is fail-loud

The secrets log (`~/.dhc/secrets/secrets.log`) is `chmod 600` on
write. If the file exists with a permissive mode (e.g. `0o644` from
a migration), the C1 service:

- Logs a warning at startup (does not crash; the user may be
  migrating from an old installation).
- Marks the service `is_insecure_permissions = True`.
- Returns `403` for `PUT` and `DELETE` (read-only `GET` still works
  so the user can see what they have).

This is the GitHub CLI lesson: silent fallback to permissive mode
is rejected; the user is told to `chmod 600` and restart. (gh's
criticism: it silently falls back to plaintext when the OS keyring
is unavailable. We do not silently fall back; we hard-fail writes.)

### 7. The accordion default-state rule (v1.5.1.1 polish)

When the modal is opened from the welcome view (no active session),
all four provider accordions collapse to the "Not set" pill, even if
keys are stored. The reasoning: the user is at a "general" entry
point and the visual noise of four open accordions (one per provider,
each with sub-accordions) is unhelpful. When the modal is opened
from an active session, only that session's provider is expanded
(others collapse). The user can still click to expand.

This is a UI-only change. The data layer is unaffected.

## Considered options

### Option A — Re-display the value in the input (v1.5.1 behavior)

Pros: trivial, matches the v1.3.x "show me my key" UX.
Cons: the value crosses the network in the `GET` response body; it
is in the browser's heap, in the React DevTools tree, in any
extension that watches fetch responses, in any screenshot of the
modal, in any screen-share. **Rejected** — the GitHub-shaped
metadata-only read is the correct discipline even in the
OpenCode quadrant, because the cost (an extra paste) is low and the
benefit (smaller blast radius) is real.

### Option B — Allow re-display behind a per-row "show" toggle

Pros: the v1.5.1 "show/hide eye" already exists.
Cons: even with a hide-by-default, the toggle lets an attacker (or
snooping extension) flip it. The toggle should reveal the *typed*
draft, not the stored value — which means the toggle is useless for
re-display. **Rejected** — the eye toggle is preserved, but it
operates on the draft, not the stored value.

### Option C (chosen) — Metadata-only GET, no prefill, hint in pill

Pros: minimal data crosses the boundary. The "Configured" / "Not set"
pill + 7-char hint is enough for the user to confirm state.
Cons: the user must always paste a key to update. This is the
correct friction — rotation should be intentional. **Adopted.**

### Option D — Bulk `PUT /api/secrets` for atomic multi-key save

Pros: one round-trip for "save all four providers."
Cons: breaks the GitHub-shaped per-name contract. The bulk endpoint
is harder to audit (one bad payload nukes four keys). The
`PUT /api/secrets/{name}` shape is the industry verb; converging on
it is cheaper than maintaining a bulk shape. **Rejected.**

## Consequences

### Positive

- The Settings modal is now safe to ship behind a remote-access
  scenario (e.g. a future "view settings from a friend" link) —
  the GET response never carries key material.
- The 0600 enforcement closes the silent-fallback path that the
  GitHub CLI was criticized for.
- The GitHub verb shape means the existing `PUT /api/secrets/{name}`
  and `DELETE /api/secrets/{name}` routes do not need to change —
  the frontend's drift was the bug.
- The hint field gives the user a quick visual confirmation ("oh,
  that's the key I set last week") without exposing the value.
- The accordion default-state change reduces visual noise in the
  general view (the welcome screen, the post-session view).

### Negative

- The user must paste a key every time they want to rotate. This
  is the correct friction, but it is a friction; users coming from
  v1.5.1 may notice.
- The `isDirty` check is now `draft != ""` rather than
  `draft != savedValue`. This is a behavior change documented in
  the v1.5.1.1 test suite.
- The hint field is a 7-character substring. If the user has many
  keys with the same first 3 + last 4 (e.g. rotated keys that all
  end in the same 4 chars because the org standardizes the suffix),
  the hints look identical. This is an acceptable trade-off — the
  hint is for confirmation, not identification.

### Risks

- A user with a `secrets.log` that was created with `0o644` (e.g.
  by an older version of the harness that didn't `chmod` on write)
  will get a startup warning and a 403 on every save until they
  `chmod 600` and restart. This is a one-time migration. The DoD
  test (`test_put_rejected_with_permissive_mode_403`) covers the
  fix path.
- The "always paste" friction may push users toward keeping keys
  in their clipboard manager, which is a separate threat surface.
  This ADR does not address clipboard hygiene; the OpenCode issue
  tracker has the discussion.

## Compliance

- `tests/chat/test_secrets_metadata_v1511.py` — 10 new Python tests:
  metadata shape, value never returned, hint shape, round-trip
  PUT/GET, round-trip DELETE/GET, 0600 fail-loud for PUT, 0600
  fail-loud for DELETE, missing-file no-op, permissive-mode raise.
- `apps/web/src/__tests__/SettingsModal.test.tsx` — 4 new vitest
  tests + 3 existing tests updated to expect `PUT`:
  metadata shape no-prefill, non-empty draft is save intent,
  no-active-session collapse, active-session open one provider.
- `dod_verify.ps1` — no new DoD check for v1.5.1.1 (the metadata
  contract is verified by the test suite, not by the DoD; the
  DoD is for architecture invariants, not API contracts). The
  v1.5.1.1 hotfix keeps the 16/16 DoD baseline.
- The v1.5.0 → v1.5.1.1 server route is unchanged. The v1.5.1
  frontend was the side that drifted; the fix is one line in
  `SettingsModal.saveKey` (change `method: "POST"` to `method: "PUT"`,
  put the name in the URL).
- The 0600 check is performed at startup in `GuiWebCore`; the
  `SecretsService` exposes `check_permissions()` and
  `is_insecure_permissions` for the route handlers to consult.
- The metadata GET is the new default shape; `names` is preserved
  for v1.5.0 callers.
- No new ADRs are required; this amends ADR-0007.

## Migration

There is **no migration** of stored keys. The encryption format is
unchanged. The only user-visible change is the modal UX (no
prefilled input, hint in the pill).

The 0600 check is a one-time user action: if the secrets log was
created with a permissive mode, run `chmod 600 ~/.dhc/secrets/secrets.log`
and restart the harness. New writes will be 0600 automatically.

## Reference

- v1.5.1 ship log: `relay/harness_benchmark-v1.5.1-20260909.zip`
- v1.5.1.1 ship log: `relay/harness_benchmark-v1.5.1.1-20260909.zip`
- v1.5.1.2 ship log: `relay/harness_benchmark-v1.5.1.2-20260909.zip`
- GitHub secrets API: `PUT /repos/{owner}/{repo}/actions/secrets/{name}`
  (encrypted client-side with libsodium sealed box).
- DeepSeek: shown-once, named, rotate via overlap.
- OpenCode: plaintext `~/.local/share/opencode/auth.json` (the
  anti-pattern that this ADR pushes back against).

## v1.5.1.2 amendment — hint redaction, sanitized errors, path self-heal

The 2026-09-09 audit hotfix found three issues that the v1.5.1.1
contract did not address:

### 1. The hint shape leaked the provider prefix

The v1.5.1.1 hint was `first_3 + … + last_4` (e.g. `sk-…7890`).
The 3-char prefix is uniformly `sk-` across OpenAI, Anthropic, and
OpenRouter, so it did not actually distinguish the provider on its
own — but it was a 3-character confirmation that *something starting
with `sk-`* was stored. The Settings modal's subtitle claimed
"Encrypted at rest, never sent to the browser in any response",
which was misleading under the prefix-leak.

**Amendment**: the hint shape is now `… + last_4` only (e.g.
`…7890`). The 5-char total is still informative ("the last 4 chars
are `7890`") and unguessable. The `updated_at` field remains a
separate signal for the user to confirm the right key was last
touched.

The `_hint_for` helper is the only function affected. The metadata
endpoint shape is unchanged:

```json
GET /api/secrets
{
  "secrets": [
    {
      "name": "llm_provider_openai_gpt-4o-mini",
      "configured": true,
      "updated_at": 1788460681.44,
      "hint": "…7890"
    }
  ],
  "names": ["llm_provider_openai_gpt-4o-mini"]
}
```

### 2. The `fetchJson` helper echoed the raw server body

The previous `fetchJson` (in both `SettingsModal.tsx` and
`ModelConfigMenu.tsx`) interpolated the raw aiohttp response body
into the thrown `Error`. For a 500, the user saw:
`Error: 500: 500 Internal Server Error\nServer got itself in
trouble`, which (a) is useless and (b) could leak file paths from
a future `PermissionError` raised by a config or secrets handler.

**Amendment**: a shared helper at
`apps/web/src/utils/sanitizeError.ts` maps HTTP statuses to friendly
text without echoing the raw body. Both `SettingsModal` and
`ModelConfigMenu` now use the shared helper. The mapping is:

| Status | Friendly text |
|--------|---------------|
| 401/403 | "Not authorized. Check the bearer token or restart the server." |
| 404 | "Not found." |
| 409 | "Conflict — try again." |
| 429 | "Too many requests. Slow down and try again." |
| 5xx | "Server is busy. Try again or restart the server." |
| 4xx | `Request failed (HTTP {status}).` |
| Network | "Network error." |

The raw body is still passed to the thrown `Error` as the `cause`
for tests and devtools, but it is not interpolated into the
user-visible string.

### 3. The `_atomic_write_json` helper assumed the parent dir exists

The constructor's `mkdir(parents=True, exist_ok=True)` only runs
at startup. If the on-disk sessions directory is mutated after
startup (a test cleanup, the v0x03→v0x04 migration, a manual `rm`),
the next write raises `FileNotFoundError`, the route 500s, and the
React client sees "click does nothing".

**Amendment**: `_atomic_write_json` now re-creates the parent dir
on every write:

```python
def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(...)
    os.replace(tmp, path)
```

This makes the path robust to any prior on-disk state. The fix is
in `src/dhc/services/session_manager.py`.

### 4. The CLI default for `--sessions-dir` was the leaf, not the parent

`serve_c1.py` defaulted `--sessions-dir` to
`repo_root/.dhc/sessions`, but `SessionManager` appends `/ "sessions"`
internally, so the on-disk layout was `repo_root/.dhc/sessions/`
which is correct — except that the doubled segment was visible in
the path printed by the migration scripts and the smoke runner,
causing operator confusion.

**Amendment**: the CLI default is now `repo_root/.dhc` (the parent
that `SessionManager` expects). The on-disk layout remains
`repo_root/.dhc/sessions/`. The 4 chat-ws test fixtures and the
smoke runner were updated to pass `tmp_path` (the parent) directly
instead of `tmp_path / "sessions"` (which produced the doubled
`tmp_path/sessions/sessions/` layout in those tests).

### 5. The `newSession` handler silently swallowed 500s

`ChatPanel.newSession` did `if (!r.ok) return null;` on a 500,
which produced no UI feedback. The user saw the click as
"does nothing".

**Amendment**: `newSession` now maps 4xx/5xx to friendly text and
sets a `newSessionError` state, which is rendered inline in the
rail below the "New chat" pill. The user can dismiss the error
manually; it is also cleared on the next successful create.

### Compliance

After the v1.5.1.2 amendment:

- The modal's "Encrypted at rest, never sent to the browser in
  any response" subtitle is **literally true** — the only fields
  in the metadata response are `name`, `configured`, `updated_at`,
  and a 4-char-tail hint. No provider prefix, no value, no body
  fragment.
- Server-side errors are not echoed to the browser; the user sees
  only the friendly text.
- The on-disk path convention is `<parent>/sessions/` (no doubled
  segment).
- Session-create failures are surfaced as inline UI feedback, not
  silent failures.

The audit table is in `CHANGELOG.md` under v1.5.1.2.

## v1.5.1.3 amendment — SecretsService self-heal

### Background

v1.5.1.2 fixed the same class of `FileNotFoundError` in
`SessionManager._atomic_write_json` (the "click does nothing"
session-create bug) but missed the **parallel write path** in
`SecretsService.put_raw` and `SecretsService.delete`. The
constructor's `mkdir` only ran at startup; any post-startup
mutation (a test cleanup, a manual `rm`, a migration that
touched the dir) left the next write hanging.

The user-visible failure mode was: after a state mutation,
`PUT /api/secrets/{name}` returned 500. The v1.5.1.2
sanitized-error helper mapped the 500 to a friendly "Server
is busy. Try again or restart the server." — but the
underlying bug was unfixed. A user clicking Save after a
state mutation saw the friendly text and concluded the
server was down, when the actual cause was a missing
directory.

### Decision

Introduce `_append_log`, a private helper on
`SecretsService` that re-creates the secrets dir + log
file before every write. The helper runs inside the
existing `self._lock`, so concurrent PUTs from multiple
tabs still serialize — no interleaved bytes, no partial
writes. The new code path:

```python
def _append_log(self, record: dict[str, object]) -> None:
    self._log.parent.mkdir(parents=True, exist_ok=True)
    with self._log.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
```

The two write sites — `put_raw` and `delete` — are
refactored to call `_append_log` instead of the bare
`self._log.open("a", ...)` pattern. The behavior of the
public API (`put`, `get`, `delete`, `list`, `list_metadata`)
is unchanged.

### Why a per-write `mkdir` and not a startup-time check

The constructor's `mkdir(parents=True, exist_ok=True)` is
still there — it's a best-effort for the happy path. The
per-write self-heal is the **authoritative** contract:
the write site is the only place that can guarantee the
path is writable. Any other approach (a periodic
background task, a "dir is present" cache, a write-ahead
log) introduces a window where the user can observe a
state mismatch. A per-write `mkdir` is idempotent and
costs ~10µs on warm cache.

### What is NOT changing

- The `self._lock` is unchanged. The lock was already held
  around the file write; v1.5.1.3 does not introduce a new
  lock. The audit raised a concurrency concern about
  `open("a")` interleaving — the existing lock already
  serializes the writes, so there is no interleaving hazard
  to fix.
- `load_or_create_master_key` is unchanged. The
  `_ensure_dir(key_path)` call at line 506 already
  self-heals the master-key path via
  `p.parent.mkdir(parents=True, exist_ok=True)`. The audit
  suggested an additional check here; it was already
  present.
- The v0x04 envelope is unchanged. The new helper is a
  write-path concern; the envelope format is orthogonal.
- The C1 route surface is unchanged. The frontend sanitized
  error helper from v1.5.1.2 still applies; with the
  underlying fix, the user will see the friendly text less
  often (because the PUT succeeds) but the helper is still
  the right defense.

### Tests

Five new tests in
`tests/chat/test_secrets_self_heal_v1513.py`:

1. `test_put_raw_self_heals_missing_secrets_dir` — wipe
   the dir after init, `put` succeeds, the dir + log
   file are recreated, the value round-trips through
   `get`.
2. `test_delete_self_heals_missing_secrets_dir` — wipe
   the dir, re-`put` (which self-heals), then `delete`
   (which also self-heals). Two log records: one
   `set`, one `del`.
3. `test_self_heal_survives_full_state_wipe` — round-trip
   three values, wipe the dir, round-trip a fourth. The
   log is rebuilt from scratch; the old values are
   gone.
4. `test_self_heal_works_when_secrets_dir_never_existed_at_init`
   — wipe the dir before the first write. The first
   write self-heals.
5. `test_concurrent_puts_dont_interleave` — pins the
   `self._lock` contract. 4 threads × 25 puts each,
   all 100 log lines parse as JSON, no partial writes.

### Invariants

The 11 new invariants in `scripts/invariants_check.ps1`
under the v1.5.1.3 section pin the contract:

- `_append_log` helper exists and self-heals the parent
  dir.
- `put_raw` does NOT use the bare `self._log.open(...)`
  pattern.
- `put_raw` calls `_append_log`.
- `delete` does NOT use the bare `self._log.open(...)`
  pattern.
- `delete` calls `_append_log`.
- Each of the 5 new test names is present in the test
  file.

### Compliance

After the v1.5.1.3 amendment:

- A user clicking Save in the Settings modal after the
  secrets dir is gone (e.g. after a manual `rm` or a
  test cleanup) sees a successful save, not a 500.
- The dir + log file are recreated on the first write.
  Subsequent writes are no-ops for the self-heal
  (`exist_ok=True`).
- Concurrent PUTs from multiple tabs still serialize
  (the `self._lock` contract is unchanged).
- The v1.5.1.2 sanitized-error helper remains the
  defense for any other 500 path (it is not relied on
  for the secrets self-heal case, but it is still the
  right user-facing mapping for any other 5xx).

The audit table is in `CHANGELOG.md` under v1.5.1.3.

## v1.5.1.4 amendment — per-provider real connection probe + inline model picker

### Background

The v1.5.1 `GET /api/llm/health` only confirmed the
harness was started with `--llm-base-url`; it did NOT
verify the user's OpenRouter (or any other) key actually
worked. The Settings modal rendered a "Configured" pill
with no way to actually confirm the key was valid. The
WelcomeView's `llmOk` state was set by this same
non-diagnostic health check, so the user always saw
"Ready" — even if their OpenRouter key was bogus.

A second UX gap: the model picker (`<ModelSelect />`)
was rendered in the chat header only when there was an
active session. With no session, the user had no way
to pick a model. The Settings modal's per-provider
accordions were also collapsed when no session was
active, even if a key was stored.

### Decision

**1. Per-provider real connection probe.** Add
`GET /api/llm/health/{provider}`. For OpenRouter, the
handler calls the canonical
`GET https://openrouter.ai/api/v1/auth/key` endpoint
(the OpenRouter-documented "is my key valid" probe),
with the user's stored key from the v1.3.2 lookup
chain. Returns:

- `{ok: true, provider, label, limit, is_free_tier}` on 200
- `{ok: false, provider, error: "invalid_key"}` on 401
- `{ok: false, provider, error: "no_key"}` when no key
- `{ok: false, provider, error: "network"}` on any
  other failure (timeout, DNS, TLS, etc.)
- `{ok: false, provider, error: "not_supported"}` for
  providers other than OpenRouter (the v1.6.0 model
  catalog will replace this with a unified per-provider
  probe layer)

The key is **never** echoed in the response body or
logged. The Authorization header sent to OpenRouter
contains the user's key (this is required for the
probe), but the response body only contains
`{ok, label, limit, is_free_tier}` on 200 — not the
key itself. The 5-second timeout (`_PROBE_TIMEOUT_SECONDS`)
prevents a slow upstream from blocking the UI thread.

**2. "Test connection" button in the Settings modal.**
Each per-provider accordion now has a "Test connection"
button (rendered only when a key is stored for that
provider) that calls the new probe and renders the
result inline. The button is disabled while the probe
is in flight (the loading state shows "Testing…"). A
failed health check does NOT invalidate the
already-saved key — the key might be valid for a
different endpoint, or OpenRouter might be temporarily
down. The button is purely a diagnostic; the user
retains the saved key regardless of the probe outcome.

**3. Inline model picker in the chat header (no-session
state).** When no active session exists, the chat
header now renders a "Default model" picker (a thin
wrapper around the existing `<ModelSelect />`). The
user's pick is persisted in `dhc.defaultModelId`
(localStorage) and sent as `{model: "..."}` in the
POST body when creating a new session. The server
validates the model id against the closed
`ModelRegistry` (400 on unknown) so an attacker cannot
inject arbitrary model strings.

### What is NOT changing

- The `ModelRegistry` is unchanged — still the v1.5.0
  6-model hardcoded list. v1.6.0 will replace this with
  a live OpenRouter catalog overlay.
- The `WelcomeView`'s "Ready" / "Offline" subtitle is
  unchanged. The inline picker + Settings Test
  connection button already answer the user's "am I
  connected?" question. v1.6.0 will wire the
  WelcomeView to a richer provider-health surface.
- The v0x04 envelope is unchanged. The new endpoint
  is a read-only key validation probe; the envelope
  format is orthogonal.
- The C1 route surface is unchanged. The new route
  reuses the existing `Capability.LLM_HEALTH`
  capability; the path-parameter variant is the same
  threat model (a read-only key validation probe).

### Tests

**5 new Python tests** in
`tests/chat/test_llm_health_v1514.py`:

1. `test_llm_health_openrouter_200_returns_label_and_limit`
   — a 200 from OpenRouter /auth/key maps to
   `{ok: true, label, limit, is_free_tier}`.
2. `test_llm_health_openrouter_401_returns_invalid_key`
   — a 401 maps to `{ok: false, error: "invalid_key"}`.
   The key is NOT in the response body.
3. `test_llm_health_openrouter_no_key_returns_no_key`
   — when no key is configured, the probe short-circuits
   without making any upstream call.
4. `test_llm_health_openrouter_network_failure_returns_network`
   — a network/timeout/DNS failure maps to
   `{ok: false, error: "network"}` without surfacing
   the exception to the client.
5. `test_llm_health_openai_returns_not_supported` —
   OpenAI does not expose a public /auth/key probe.

**5 new vitest tests** in
`apps/web/src/__tests__/SettingsModal.test.tsx` and
`apps/web/src/__tests__/ChatPanel.test.tsx`:

1. `auto-expands an accordion when a key is stored and
   there is no active session` (SettingsModal)
2. `renders the Test connection button in the loading
   state while the probe is in flight` (SettingsModal)
3. `renders the error line when the probe returns
   invalid_key` (SettingsModal)
4. `does NOT render the Test connection button when no
   key is stored for the provider` (SettingsModal)
5. `renders the default-model pill in the no-session
   state` (ChatPanel)

### Invariants

The 19 new invariants in
`scripts/invariants_check.ps1` under the v1.5.1.4
section pin the contract:

- The new route is registered.
- The handler exists and uses the canonical
  `https://openrouter.ai/api/v1/auth/key` URL.
- The 401 path is mapped to `error: "invalid_key"`.
- The new test file + 5 test names are present.
- The Settings modal renders the "Test connection"
  button, wires the handler, and calls
  `/api/llm/health/{provider}`.
- The ChatPanel renders the `chat-model-pill-default`
  data-testid, persists `dhc.defaultModelId` in
  localStorage, and sends it in the POST body.
- The server's `_api_sessions_create` accepts an
  optional `model` field.
- Both vitest describe blocks exist.

### Compliance

After the v1.5.1.4 amendment:

- A user who has saved an OpenRouter key can click
  "Test connection" in the Settings modal and see
  `Connected as "their-label"` (or `Invalid API key`).
- A user with no active session sees a "Default model"
  picker in the chat header. Picking a model
  pre-selects it for the next session they create.
- The chat header no longer pretends to know whether
  the user's key works (the v1.5.1 fake health check
  is preserved at `/api/llm/health` for backward
  compat, but the per-provider probe is the source of
  truth for "is my key valid?").
- A failed health check does NOT invalidate the
  already-saved key.

The audit table is in `CHANGELOG.md` under v1.5.1.4.

## v1.5.1.5 amendment — C7 streaming timeouts + source-level log invariants

### Context

The 2026-09-09 live verification
(`docs/verification/2026-09-09-openrouter-probe.md`)
exercised the v1.5.1.4 `raw` secret path against a real
OpenRouter key. The no-leak contract held at all 8 tested
boundaries (disk artifact, server stdout, server stderr,
served HTML, served bundle, all 9 HTTP endpoints, the probe
response body, the `Authorization` header transport). The
verification also flagged 4 findings:

1. **No ingress scrubber for WebSocket chat frames** —
   a user pasting a key into the chat input would have it
   sent upstream and persisted in the session journal.
2. **C7 dispatch hangs on `mock-llm/default`** — a 60s
   smoke test produced no `chat.delta` / `chat.done`
   response.
3. **Loopback network isolation** — the test environment
   could not reach `openrouter.ai`, blocking the live
   probe verification path.
4. **No source-level invariant pins the absence of the
   key or upstream response body from server logs.**

### Findings 1, 2, 3, 4 — actual disposition

- **Finding #1 (no ingress scrubber)**: **REAL.** Deferred
  to v1.6.0 Phase 0 (`IngressScrubber` is one of the 8
  Phase 0 deliverables per the verification lock).
- **Finding #2 (C7 dispatch hang)**: **FALSE POSITIVE.**
  Root cause was a test-client bug — the client awaited a
  HELLO frame the server never sends on `/ws/chat`. The
  actual C7 streaming path works correctly end-to-end
  (6 chat.delta frames + chat.done with token accounting,
  ~88ms latency, verified after the verification with a
  corrected test client that does not expect HELLO). The
  C7 adapter is NOT broken in v1.5.1.4. The verification
  evidence file has been updated to mark Finding #2 as
  FALSE POSITIVE.
- **Finding #3 (loopback network isolation)**: **REAL,
  ENVIRONMENT.** This is a property of the test machine
  (the loopback cannot reach `openrouter.ai`), not a code
  defect. The 8 tested no-leak boundaries are still
  meaningful: the probe was attempted (the server
  constructed the `Authorization` header), the transport
  failed before the wire, and the server returned
  `{"ok": false, "error": "network"}` without echoing the
  key. No code change for this finding; future
  verifications must run on a machine with outbound
  network access.
- **Finding #4 (no source-level log invariants)**: **REAL.**
  v1.5.1.5 ships the 2 invariants.

### Decision

v1.5.1.5 ships two changes:

1. **C7 streaming timeouts (defense in depth)** — the
   `_api_llm_health_provider` handler and the mock
   LLM are not the only paths that touch the key. The C7
   dispatch (openai_compatible chat_stream) also reads
   the key from `secrets_service` and constructs an
   `Authorization` header. A deadlock in the streaming
   iterator would hang the WS for 30+ seconds without
   any user-visible feedback. v1.5.1.5 adds explicit
   per-stream timeouts:
   - **Mock path**: 5-second per-chunk read timeout on
     `httpx.Timeout` PLUS a 5-second wall-clock cap on
     the whole stream via `asyncio.timeout`.
   - **Live path**: existing 30-second per-chunk read
     timeout PLUS a 30-second wall-clock cap.
   - On timeout, the C7 adapter raises
     `asyncio.TimeoutError`; the WS handler in
     c1_gui_web_core catches it and emits
     `{"type": "chat.error", "code": "stream_timeout"}`.
   - Constructor accepts `stream_timeout_s` (default
     30.0) and `mock_stream_timeout_s` (default 5.0);
     callers can override per-call via
     `chat_stream(..., stream_timeout_s=...)`.

2. **Source-level log invariants** — the
   `_api_llm_health_provider` handler in
   `c1_gui_web_core/service.py` must not log the API
   key or the upstream response body. v1.5.1.5 adds 2
   invariants to `scripts/invariants_check.ps1` that
   extract the handler's function body and grep for
   `logger.*(api_key|api_key_str|Authorization)` and
   `logger.*(resp.text|resp.content|response.text|response.content|body)`.
   A regression that adds `logger.warning("upstream: %s",
   r.text)` would fail the build at CI time.

### What is NOT changing

- The C7 dispatch logic itself is unchanged. The
  streaming path worked correctly in v1.5.1.4 (Finding
  #2 is a false positive); v1.5.1.5 only adds the
  timeout as a safety net.
- The v0x04 envelope is unchanged.
- The metadata-only `GET /api/secrets` is unchanged.
- The `KEY_NOT_LOGGED` invariant from the v1.5.1.1
  amendment is unchanged.
- The `Settings "Save"` 500 fix from v1.5.1.2 is
  unchanged.
- The `_append_log` self-heal from v1.5.1.3 is
  unchanged.
- The per-provider probe from v1.5.1.4 is unchanged.

### Tests

- `tests/chat/test_c7_stream_timeout_v1515.py` (NEW,
  4 tests):
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

### Invariants

14 new v1.5.1.5 invariants in
`scripts/invariants_check.ps1` (v1.5.1.5 section):
- 6 timeout contract invariants (`_stream_timeout_s` field
  exists; `_mock_stream_timeout_s` field exists; default
  30.0; default 5.0; `_consume_with_timeout` helper
  exists; `_dispatch_mock` split-out exists).
- 1 WS-handler mapping invariant (`stream_timeout` code
  in the `except Exception` block).
- 5 regression-test name invariants (test file + 4 names).
- 2 no-leak log invariants (`api_key` / `Authorization` /
  `api_key_str` not in `_api_llm_health_provider`; `resp.text`
  / `resp.content` / `body` not in `_api_llm_health_provider`).

### Compliance

After the v1.5.1.5 amendment:
- A future regression that deadlocks the C7 streaming
  iterator surfaces as `chat.error` /
  `code: "stream_timeout"` in <5s (mock) or <30s
  (live) instead of hanging the WS.
- A future regression that adds
  `logger.warning("upstream: %s", r.text)` to
  `_api_llm_health_provider` is caught at CI time by
  the invariants check.

The verification record is in
`docs/verification/2026-09-09-openrouter-probe.md`,
updated to mark Finding #2 as FALSE POSITIVE.
