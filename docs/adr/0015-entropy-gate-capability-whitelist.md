# ADR-0015: Entropy Gate — Capability Whitelist & Default-Deny Policy-as-Data

- **Status:** Accepted 2026-09-07
- **Supersedes:** none
- **Superseded by:** none
- **Related:** ADR-0016 (Number Gate, legal charset), ADR-0017 (tests as error boundary, Entropy Map)

## Context

v1.3.x closed three classes of attack on the secrets layer (confused-deputy via name-swap, field-boundary shift in the MAC input, downgrade via anonymous v0x03 envelopes). The C1 HTTP/WebSocket surface, however, is a single large `add_*` block in `service.py` with 22 routes and one token-equivalent helper (`require_token`). There is no central policy:

- Each route decides for itself whether to require a token. The check is scattered.
- "What can the loopback client do?" has to be answered by reading every route, not by reading a single table.
- "Did we add a new route that bypasses the token check?" cannot be detected at invariant time, only at audit time.

The runtime guard (the token check) and the test suite (the route tests) are two separate things maintained by hand. They can drift. A new route that omits the token check is a silent regression.

v1.4.0 closes this with **policy-as-data**: one `ACTION_WHITELIST` table is the single source of truth. The runtime guard reads it. A test generator iterates it. An Entropy Map generator renders it. Three projections, one source, generator-enforced.

## Decision

CREC (Controlling Range of Entropy Chaos) is the codename. The **Entropy Gate** is the component.

### The principle

1. **Finite, enumerable action space.** Every legal operation is a named `Capability` on a whitelist. Default-deny. Anything not on the list is rejected, logged, and becomes an audit signal.
2. **All data is canonicalized to numbers.** Bytes → integer codes. Text is only ever a *projection* of those numbers, and the legal text alphabet is `[A-Z a-z 0-9]` (62 codes). See ADR-0016.
3. **Entropy is bounded so it is auditable.** The space is small and finite (256 byte codes, N capabilities, N ≤ 24 for v1.4.0). An agent can traverse *every* option and classify its risk. You cannot audit infinite chaos; you can audit a closed set.
4. **Expand only on stable ground.** Adding a capability requires an ADR + a whitelist entry + its pass/deny tests. Growth is gated.

### The capability budget

**The cap is the current route count + 4 headroom = 24 for v1.4.0.** The cap is enforced by an invariant: `len(ACTION_WHITELIST) <= 24`. Adding a route above that requires an ADR that justifies the new capability and (if the cap is being hit) proposes the merge or the cap relaxation.

The reason for the "current count + headroom" framing: a static cap of 20 is the wrong number because the current surface is already 20, which means a single new feature forces a capability merge that loses the audit signal. The cap should be the *enforcement* of the principle, not a number chosen at random.

### The capability table (v1.4.0)

`ACTION_WHITELIST: dict[Capability, ActorTier]` is the single source of truth. The full table is generated from the v1.4.0 route inventory and frozen at the v1.4.0 release.

```python
class Capability(str, Enum):
    # Secrets (3)
    SECRET_PUT  = "secret.put"
    SECRET_LIST = "secret.list"
    SECRET_DELETE = "secret.delete"
    # Config (2)
    CONFIG_GET  = "config.get"
    CONFIG_SET  = "config.set"
    # Sessions (5)
    SESSION_LIST   = "session.list"
    SESSION_CREATE = "session.create"
    SESSION_READ   = "session.read"
    SESSION_PATCH  = "session.patch"
    SESSION_DELETE = "session.delete"
    # Chat (1; HTTP + WS share the same capability)
    CHAT_SEND   = "chat.send"
    # Models (2)
    MODEL_LIST  = "model.list"
    MODEL_READ  = "model.read"
    # Manifest (1)
    MANIFEST_READ = "manifest.read"
    # Prompts (1)
    PROMPT_READ = "prompt.read"
    # Plugins (3)
    PLUGIN_LIST   = "plugin.list"
    PLUGIN_LOAD   = "plugin.load"
    PLUGIN_UNLOAD = "plugin.unload"
    # Eval (1)
    EVAL_RUN    = "eval.run"
    # LLM health (1)
    LLM_HEALTH  = "llm.health"
```

That's **20 capabilities** on the current surface, with 4 headroom slots for v1.4.0 follow-ups. The 4 headroom slots are reserved for capabilities the implementation has not yet added (e.g. `attachment.put` for v1.5.0). Adding a real capability beyond the 24 cap requires a new ADR.

`ActorTier` is the per-capability authorization policy. The current harness has one tier (`LOOPBACK`, meaning "the loopback bind is the perimeter; all callers are equally trusted"). The enum is reserved for future multi-tier deployments.

```python
class ActorTier(str, Enum):
    LOOPBACK = "loopback"  # the only tier in v1.4.0; reserved for future
```

### The route → capability map

Each `add_*` route is wrapped with a decorator that:
1. Looks up the route's `Capability` in `ACTION_WHITELIST`.
2. Rejects with `CapabilityDenied` if the capability is not in the whitelist (default-deny: a route without a registered capability is rejected at startup, not at request time — see the runtime-guard invariant).
3. Executes the handler. Failures (existing `ValueError`, `SecretEnvelopeError`, etc.) are caught and rendered as typed errors at the audit boundary.

```python
@requires(Capability.SECRET_PUT)
async def _api_secrets_put(request: web.Request) -> web.Response:
    ...
```

The decorator is the AUTHORIZE stage of the pipeline. If the capability is not in `ACTION_WHITELIST`, the request never reaches the handler. The audit emit happens after the handler returns (success or failure).

### The runtime guard (one source, three projections)

The runtime enforcer reads `ACTION_WHITELIST`. The test generator iterates `ACTION_WHITELIST` and emits one test per capability × actor-tier combination (pass for authorized, deny for unauthorized, raise for unknown actor tier). The Entropy Map generator iterates the same table and emits `docs/entropy-map.md`. See ADR-0017 for the generator pattern.

### Default-deny at startup

A route registered via `add_*` *without* a `@requires(...)` decorator fails the startup invariant. This is the "default-deny at the framework boundary" rule: a new route cannot ship without a capability, and a capability cannot ship without a whitelist entry. The invariant is checked in `scripts/invariants_check.ps1` and is a hard DoD check.

## Consequences

### Positive

- **Single source of truth.** `ACTION_WHITELIST` is the table that the runtime, the tests, and the audit doc all read. Adding a capability requires editing one place.
- **Generator-enforced coverage.** The test suite is a thin pytest wrapper over `enumerate_capability_tests()`. A new capability automatically gets a pass-test, a deny-test, and a raise-test. There is no hand-written test list to drift.
- **Audit boundary is renderable.** `docs/entropy-map.md` is generated from the same table, so the doc cannot drift from the code.
- **Default-deny at startup.** A route without a registered capability fails the DoD. A new route cannot ship by accident.
- **Bounded entropy.** The capability set is finite (≤ 24 for v1.4.0) and enumerable. The agent can traverse every option and classify its risk.

### Neutral

- The `Capability` enum is a `str, Enum` so its values are valid Python identifiers and serializable to JSON. The runtime guard uses string equality, not enum equality, so the audit log shows the human-readable capability name.
- The `ActorTier` enum has only one value (`LOOPBACK`) in v1.4.0. It is reserved for future multi-tier deployments (e.g. `LOOPBACK` for the local browser, `OPERATOR` for an admin endpoint, `EXTERNAL` for a webhook).
- The decorator adds one function-call layer per request. The cost is one dict lookup + one enum comparison, both O(1). No measurable performance impact.

### Negative

- **The capability table is a coupling point.** Every route registration must list its capability. This is the price of policy-as-data; without it, the policy is the scattered union of every route's `if` statement.
- **The 24-cap cap is arbitrary.** It is the *current surface + 4 headroom* and is documented as such. A future maintainer who hits the cap must add an ADR.
- **The audit log is the only place that sees the 62-code projection** (see ADR-0016). The user-input path is unchanged; the user can type any byte. This is a security boundary, not a user-facing constraint.

## Wire format (unchanged)

No wire-format change. The capability is enforced at the C1 service's request handler; the client never sees a capability string. The audit log entries gain a new optional field `capability: str` in v1.4.0 (the name of the capability that the request was authorized under). Existing log readers ignore the new field.

## Test plan

8 new tests in `tests/cordis/test_capabilities.py`:

1. `test_capability_enum_is_finite` — `len(Capability) <= 24`. (Static shape.)
2. `test_action_whitelist_keys_are_subset_of_capability_enum` — every key in `ACTION_WHITELIST` is a valid `Capability`. (Static shape.)
3. `test_capability_decorator_allows_authorized_actor` — parametrized over `Capability` × `ActorTier`. (Generator-emitted.)
4. `test_capability_decorator_denies_unauthorized_actor` — same generator. The unauthorized case is "future multi-tier" (we have only one tier in v1.4.0, so the test uses a stub tier that is not in `ActorTier`).
5. `test_unknown_capability_string_raises` — a `Capability` value that is not in the enum raises `ValueError` at registration. (Static shape.)
6. `test_route_without_capability_fails_invariant` — a synthetic route registered without `@requires` is caught by the invariant checker. (Static shape — checked via `scripts/invariants_check.ps1`.)
7. `test_default_deny_for_unregistered_actor_tier` — an `ActorTier` not in the whitelist raises `CapabilityDenied`. (Generator.)
8. `test_capability_decorator_preserves_handler_return_value` — the `@requires` decorator is a transparent pass-through for the handler's return value. (Sanity.)

A second test file, `tests/cordis/test_pipeline_guard.py`, exercises the full five-stage pipeline (INGEST → AUTHORIZE → VALIDATE → EXECUTE → AUDIT) end-to-end with a synthetic capability and handler.

## Rejected alternatives

- **Decorator-free route registration (status quo).** Rejected: the current scattered `if` checks are exactly the drift the proposal closes.
- **Per-route capability strings without an enum.** Rejected: the enum is the static shape that the invariant checker and the test generator both depend on. Without the enum, the whitelist is just a dict of strings; the type-safety is gone.
- **An ACL-style whitelist with a per-route JSON file.** Rejected: a JSON file is harder to iterate in Python than a `dict[Enum, ActorTier]`. The generator pattern requires a Python source of truth.
- **Hard cap of 20 capabilities.** Rejected: the current surface is already 20. A static cap forces a capability merge that loses the audit signal. The "current count + 4 headroom" framing is the principled cap.
- **Multi-tier authorization in v1.4.0.** Rejected: the v1.4.0 deployment is loopback-only. Multi-tier is a v1.5.0+ concern when (if) external callers are added.

## Implementation notes

- `src/dhc/cordis/capabilities.py` — the `Capability` and `ActorTier` enums, the `ACTION_WHITELIST` table, the `requires` decorator, and the `CapabilityDenied` error.
- The decorator is applied at the existing C1 route-registration site (`service.py` lines 1102–1127). Each `add_*` call is preceded by a `@requires(Capability.X)` on the handler.
- The startup invariant in `scripts/invariants_check.ps1` walks the route table and asserts every registered route has a `@requires` decorator. A failure is a hard DoD fail.
- The test file `tests/cordis/test_capabilities.py` uses `pytest.mark.parametrize` driven by `enumerate_capability_tests()` (a function in `capabilities.py` that returns a list of `pytest.param` tuples, one per capability × actor-tier combination).
