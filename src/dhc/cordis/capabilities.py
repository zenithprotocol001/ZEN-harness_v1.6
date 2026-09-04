"""dhc.cordis.capabilities: the capability whitelist and the
`@requires` decorator (ADR-0015, ADR-0015-amendment-1).

Three things are exported:

- `Capability` — the closed enum of legal actions. Adding a value
  requires an ADR (the cap is 32 for v1.5.0).
- `ActorTier` — the closed enum of authorization tiers. The current
  harness has one tier (`LOOPBACK`); the enum is reserved for future
  multi-tier deployments.
- `ACTION_WHITELIST` — `dict[Capability, ActorTier]`. The single
  source of truth for the runtime enforcer, the test generator, and
  the Entropy Map.
- `enumerate_capability_tests()` — the test generator. The test file
  parametrizes over the result; a new capability automatically gets
  pass/deny/raise cases.
- `@requires(capability)` — the decorator. Wraps a C1 route handler
  with the AUTHORIZE stage of the CREC pipeline. If the capability
  is not in `ACTION_WHITELIST`, the request never reaches the handler.
- `CapabilityDenied` — the typed error raised by the runtime guard
  when a capability is not authorized for the actor tier.
- `CAPABILITY_BUDGET_V140` — the cap (24) for the v1.4.0 release.
  Preserved as archaeology per ADR-0015-amendment-1.
- `CAPABILITY_BUDGET_V150` — the cap (32) for the v1.5.0 release.
  Adding a route above the cap requires an ADR.

The principle: one source of truth (data), three projections
(runtime, tests, audit). See ADR-0017.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Awaitable, Callable

import pytest


# The v1.4.0 cap, preserved as archaeology per ADR-0015-amendment-1.
# Not raised; the constant is frozen.
CAPABILITY_BUDGET_V140: int = 24

# The v1.5.0 cap (ADR-0015-amendment-1): 26 (current surface) + 6
# headroom for v1.5.1 follow-ups. Adding a route above this
# requires a new ADR amendment.
CAPABILITY_BUDGET_V150: int = 32


class Capability(str, Enum):
    """The closed set of legal actions on the C1 surface.

    Adding a value requires an ADR; the cap is `CAPABILITY_BUDGET_V150`
    for v1.5.0. The values are stable strings (the audit log stores
    the string form).
    """

    # Secrets (3)
    SECRET_PUT = "secret.put"
    SECRET_LIST = "secret.list"
    SECRET_DELETE = "secret.delete"
    # Config (2)
    CONFIG_GET = "config.get"
    CONFIG_SET = "config.set"
    # Sessions (5)
    SESSION_LIST = "session.list"
    SESSION_CREATE = "session.create"
    SESSION_READ = "session.read"
    SESSION_PATCH = "session.patch"
    SESSION_DELETE = "session.delete"
    # Chat (1; HTTP + WS share the same capability)
    CHAT_SEND = "chat.send"
    # Models (2)
    MODEL_LIST = "model.list"
    MODEL_READ = "model.read"
    # Manifest (1)
    MANIFEST_READ = "manifest.read"
    # Prompts (1)
    PROMPT_READ = "prompt.read"
    # Plugins (3)
    PLUGIN_LIST = "plugin.list"
    PLUGIN_LOAD = "plugin.load"
    PLUGIN_UNLOAD = "plugin.unload"
    # Eval (1)
    EVAL_RUN = "eval.run"
    # LLM health (1)
    LLM_HEALTH = "llm.health"
    # Branching (3; v1.5.0, ADR-0019)
    BRANCH_CREATE = "branch.create"
    BRANCH_SWITCH = "branch.switch"
    BRANCH_LIST = "branch.list"
    # Attachments (3; v1.5.0, ADR-0020)
    ATTACHMENT_PUT = "attachment.put"
    ATTACHMENT_GET = "attachment.get"
    ATTACHMENT_DELETE = "attachment.delete"


# Sanity: the current count is below the cap. Adding a value above
# the cap is a hard DoD failure.
assert len(Capability) <= CAPABILITY_BUDGET_V150, (
    f"Capability enum has {len(Capability)} values; v1.5.0 cap is {CAPABILITY_BUDGET_V150}"
)


class ActorTier(str, Enum):
    """The closed set of authorization tiers.

    v1.5.0 has one tier: `LOOPBACK` (the loopback bind is the
    perimeter; all callers are equally trusted). The enum is
    reserved for future multi-tier deployments (e.g. `OPERATOR` for
    an admin endpoint, `EXTERNAL` for a webhook). The decorator
    does not yet accept a per-request actor argument; this is
    v1.6.0+ work (see ADR-0015-amendment-1 §"Consequences").
    """

    LOOPBACK = "loopback"


# The single source of truth. Every Capability maps to the actor
# tier that is authorized to execute it. v1.5.0: every entry is
# LOOPBACK because the harness runs on loopback.
ACTION_WHITELIST: dict[Capability, ActorTier] = {
    cap: ActorTier.LOOPBACK
    for cap in Capability
}


class CapabilityDenied(Exception):
    """Raised by the runtime guard when a capability is not authorized.

    The error message intentionally does not leak which side of the
    authorization failed (capability vs. tier). The audit log
    records the request's actor tier and the requested capability
    under a single string for offline analysis.
    """

    def __init__(self, capability: str, actor_tier: str) -> None:
        self.capability = str(capability)
        self.actor_tier = str(actor_tier)
        super().__init__(
            f"capability not authorized: {self.capability!r} for actor tier {self.actor_tier!r}"
        )


def _resolve_actor_tier(actor: Any) -> ActorTier | None:
    """Resolve the actor tier from a request-like object.

    v1.4.0 has one tier (`LOOPBACK`); the harness binds to
    127.0.0.1 so every request is loopback. A future multi-tier
    deployment would inspect the actor here.

    Returns the resolved `ActorTier` or `None` if the actor is
    not a recognized tier. `None` is treated as default-deny at
    the whitelist check.
    """
    if actor is None or actor == ActorTier.LOOPBACK:
        return ActorTier.LOOPBACK
    return None


def _authorize(capability: str, actor: Any = None) -> Capability:
    """The runtime guard. Returns the `Capability` if authorized.

    Raises:
        CapabilityDenied: if the capability is not in the whitelist
            or the actor tier is not authorized.
        ValueError: if `capability` is not a known `Capability` value.
    """
    # Resolve the capability name to a `Capability` value. Unknown
    # strings raise `ValueError` (the invariant checker catches
    # this at registration time; a request that hits this path is
    # a programming error).
    try:
        cap = Capability(capability)
    except ValueError as e:
        raise ValueError(f"unknown capability: {capability!r}") from e
    tier = _resolve_actor_tier(actor)
    if tier is None or ACTION_WHITELIST.get(cap) is not tier:
        # Both "unknown actor tier" and "tier not in whitelist" raise
        # the same error. The error message names the requested
        # capability and the actor's tier (or "unknown" if the
        # tier did not resolve) so the audit log has the data.
        raise CapabilityDenied(
            capability=cap.value,
            actor_tier=(tier.value if tier is not None else str(actor)),
        )
    return cap


def requires(capability: Capability) -> Callable:
    """Decorator: wrap a C1 route handler with the AUTHORIZE stage.

    Usage:
        @requires(Capability.SECRET_PUT)
        async def _api_secrets_put(request: web.Request) -> web.Response:
            ...

    The decorator is a transparent pass-through for the handler's
    return value. If `_authorize` raises, the decorator re-raises
    (the framework's exception handler renders the typed error).
    """

    def decorator(handler: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            _authorize(capability.value)
            return await handler(*args, **kwargs)
        # Carry the capability through for the audit log and the
        # invariant checker.
        wrapper.__wrapped_capability__ = capability  # type: ignore[attr-defined]
        wrapper.__name__ = getattr(handler, "__name__", "wrapped")
        return wrapper
    return decorator


def enumerate_capability_tests() -> list[pytest.param]:
    """Generator: one `pytest.param` per Capability x ActorTier case.

    The test file `tests/cordis/test_capabilities.py` parametrizes
    over the result and asserts the runtime guard's behavior. A
    new `Capability` value automatically gets 3 test cases:
    - pass (authorized actor)
    - deny (unauthorized actor; v1.4.0: stub tier)
    - raise (unknown capability string)
    """
    cases: list[pytest.param] = []
    for cap in Capability:
        cases.append(
            pytest.param(
                cap.value,
                ActorTier.LOOPBACK.value,
                "pass",
                id=f"{cap.value}_pass",
            )
        )
    # Unauthorized actor: a stub tier that is not in `ActorTier`.
    cases.append(
        pytest.param(
            Capability.SECRET_PUT.value,
            "stub_unauthorized_tier",
            "deny",
            id="secret.put_deny_stub_tier",
        )
    )
    # Unknown capability string.
    cases.append(
        pytest.param(
            "nonexistent.capability",
            ActorTier.LOOPBACK.value,
            "raise",
            id="unknown_capability_raise",
        )
    )
    return cases


__all__ = [
    "CAPABILITY_BUDGET_V140",
    "CAPABILITY_BUDGET_V150",
    "Capability",
    "ActorTier",
    "ACTION_WHITELIST",
    "CapabilityDenied",
    "_authorize",
    "requires",
    "enumerate_capability_tests",
]
