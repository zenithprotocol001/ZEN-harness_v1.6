# ADR-0007 — API Key Management

- **Status**: Accepted (2026-09-02); **amended 2026-09-04** (per-model keys)
- **Deciders**: harness maintainers
- **Date**: 2026-09-02 (amended 2026-09-04)
- **Supersedes**: (none)
- **Related**: ADR-0006 (Model Selection Strategy), ADR-0012 (v0x03 envelope), `docs/secrets-model.md`

## v1.3.2 amendment: per-model keys

v1.3.1 shipped the SettingsModal with one row per *provider* (the first
model of that provider drove the secret name, e.g. `llm_provider_openai_gpt-4o-mini`).
v1.3.2 lifts the per-provider ceiling and supports one row per
*`(provider, model_id)` pair*, while preserving the v1.3.1 provider-key as
a fallback.

### Naming convention (amended)

The v1.3.1 convention is unchanged:

```
llm_provider_{provider}_{model_id}
```

where `{model_id}` is the canonical model id with the provider prefix
stripped (e.g. `gpt-4o-mini` for the OpenAI model whose canonical id is
`openai/gpt-4o-mini`).

To support multiple keys for the same `(provider, model_id)` pair (e.g. a
user with two OpenAI accounts both wanting `gpt-4o-mini`), the optional
disambiguator suffix `___{n}` is appended:

```
llm_provider_openai_gpt-4o-mini            (the default key, also the fallback)
llm_provider_openai_gpt-4o-mini___2        (a second key for the same model)
llm_provider_openai_gpt-4o-mini___3        (a third key, etc.)
```

The `___n` suffix uses a triple underscore and a positive integer `n >= 2`.
The canonical name (no suffix) is always the "default" key for that
`(provider, model_id)` pair.

### Lookup order (v1.3.2)

When a provider client needs a key for `(provider, model_id)`, the lookup
order is:

1. `llm_provider_{provider}_{model_id}` (the default key for that model)
2. `llm_provider_{provider}_{model_id}___2`, then `___3`, … (additional
   keys, in numeric order, until one resolves)
3. `llm_provider_{provider}_{first_model_id}` (the v1.3.1 per-provider
   fallback, preserved for users who set a single key per provider in
   v1.3.1 and didn't migrate)
4. If none resolve: `ProviderError(401, "missing api key for {model_id}")`,
   the WS handler closes with `1011` and the React panel shows a
   "Missing API key" banner. **Identical to v1.3.1 behavior.**

### UI changes (v1.3.2)

- The SettingsModal renders one row per `(provider, model_id)` pair, drawn
  from the result of `GET /api/models`. The row is grouped under the
  provider header.
- Each row has a "Key saved" badge when the lookup resolves for that model.
- The v1.3.1 per-provider fallback is shown as a separate row at the top
  of each provider group, labelled e.g. "OpenAI (default — applies to
  all OpenAI models unless overridden)".
- The `Add another key` button under a model appends `___2`, `___3`, … to
  the secret name. Existing additional keys for that model are listed
  beneath the row with delete buttons. Disambiguator indices are not
  reused; deleting `___2` and adding again produces `___3` (or whatever
  is the next unused `n`). This avoids accidental re-use of a key the
  user thought was deleted.

### Migration

There is **no migration** of v1.3.1 keys. A v1.3.1 key named
`llm_provider_openai_gpt-4o-mini` is already a v1.3.2 "default key" for
that model. The user does not have to do anything.

### Backward compatibility

- Existing v1.3.1 names are valid v1.3.2 names (the `___n` suffix is
  optional).
- The lookup order makes the per-provider fallback explicit; this is a
  documentation clarification, not a behavior change for the
  "exactly one key per model" case (which is the v1.3.1 case).
- The React-side invariant that the SettingsModal does not use
  `window.prompt()`, `window.alert()`, or `window.confirm()` is unchanged.

### Tests (v1.3.2, 8 new)

- 3 Python tests in `tests/integrations/test_key_lookup.py`:
  exact per-model match wins, per-provider fallback when no per-model key,
  missing both → 401 (regression guard).
- 5 vitest tests in `apps/web/src/__tests__/SettingsModal.test.tsx`:
  renders one row per model, "Key saved" derives from exact key match,
  save POSTs to the per-model key, delete targets the per-model key,
  "Add another key" appends `___{n+1}`.

---

## Context and problem statement

v1.3.0 introduces live LLM providers (OpenAI, Anthropic, OpenRouter). Each call to a live provider requires a per-user API key. The v1.2.0 release already ships `SecretsService` (an HMAC-SHA256 encrypt-then-MAC envelope at `~/.dhc/secrets/secrets.log`) and the C1 routes `GET /api/secrets`, `PUT /api/secrets/{name}`, `DELETE /api/secrets/{name}`. The v1.2.0 Salt strategy section also reserves headroom for a per-secret random nonce in the envelope format.

We need to decide:

1. How are API keys for live providers stored and retrieved?
2. How is the (provider, model) → secret name mapping done?
3. What happens when a key is missing?
4. Does v1.3.0 also implement the per-secret nonce promised in v1.2.0?

## Decision drivers

- The existing `SecretsService` is already loopback-only, encrypted at rest, and has a stable HTTP surface. Reusing it is much cheaper than introducing `keyring` or another backend.
- v1.2.0 already locks the `Session.model` field; per-session model selection is therefore a no-schema-change feature.
- The chat UX must surface a missing key in a way that does not crash the WS handler.

## Considered options

### Option A — Store in OS keychain (`keyring`)

Pros: industry-standard, OS-managed.
Cons: heavy dependency, different backends per OS (Credential Manager / Secret Service / Keychain), no v1.2.x test surface to reuse. Blocked by the sandbox (no `keyring` available in the runtime).

### Option B — Store as plaintext in `~/.dhc/secrets/api_keys.json`

Pros: trivial.
Cons: a local file reader sees the keys. Rejected — the v1.2.0 threat model explicitly excludes this.

### Option C (chosen) — Reuse `SecretsService` with a per-provider naming convention

The secret name is:

```
llm_provider_{provider}_{model_id}
```

Where `{model_id}` is the part of the canonical id after the provider prefix. Examples:

- `llm_provider_openai_gpt-4o-mini`
- `llm_provider_anthropic_claude-3-5-sonnet-latest`
- `llm_provider_openrouter_auto`
- `llm_provider_mock-llm_default` (empty value; the mock doesn't need a key but the name exists so the UI shows a single list)

The factory `provider_client_for(model)` looks up the key with `secrets_service.get(name)`. If the key is missing AND the model is not the mock, `ProviderError(status=401, message="missing api key for {model_id}")` is raised. The C1 chat WS handler closes with code `1011` and a payload of `{"error": "missing api key for {model_id}"}`. The React `ChatPanel` shows a "Missing API key" banner with a `curl` hint (Settings UI is deferred to v1.3.1).

## Decision

**Option C.** The `SecretsService` is the single source of truth for API keys. The naming convention is locked by this ADR. The per-secret random nonce promised in v1.2.0's Salt strategy is **deferred to v1.3.1** to keep v1.3.0 scoped to the live-provider rollout.

## Consequences

- **Positive**: zero new dependencies. The existing 23 `test_secrets.py` tests cover the storage layer; the v1.3.0 provider client tests can use the existing `tmp_path` pattern.
- **Positive**: keys are encrypted at rest with the v1.2.0 envelope. The 23 tamper-detection tests in v1.2.0 already cover the surface.
- **Negative**: the v1.3.0 user has to `curl` keys in (or wait for v1.3.1's Settings modal). The README's "Quick start" section gets a one-liner with the exact `curl` invocation.
- **Negative**: a single user can have at most one key per (provider, model) pair. Multi-key rotation is out of scope.
- **Risk**: a user puts their OpenAI key into the wrong field (e.g. `name="openai-key"` instead of `name="llm_provider_openai_gpt-4o-mini"`). Mitigation: the v1.3.0 Settings UI (deferred) will have a dropdown of known `(provider, model)` pairs, so the field is pre-filled. Until then, the README documents the naming convention prominently.

## Compliance

- The `package_relay.ps1` exclude list must drop `~/.dhc/secrets/` if it ever appears in the repo (verify in v1.3.0 cleanup). Currently the path is not in the repo so no `.gitignore` change is required.
- The 23 secrets tests from v1.2.0 are the contract surface; no new secrets tests are added in v1.3.0.
- The chat WS handler test (`test_c7_dispatch.py::test_c7_dispatch_missing_api_key_raises`) asserts the missing-key behavior.
- A new invariant in `scripts/invariants_check.ps1` asserts that every model in `ModelRegistry` has a corresponding `(provider, model_id)` pair covered by the naming convention (i.e. `model.id.partition("/")[2]` is non-empty).
