"""dhc.cordis.pipeline_guard: the 5-stage CREC pipeline
(INGEST → AUTHORIZE → VALIDATE → EXECUTE → AUDIT).

Each stage has a typed error. The pipeline is the runtime
projection of the policy data (`ACTION_WHITELIST` and
`LEGAL_TEXT_CODES`). A test generator iterates the policy and
emits pass/deny tests for the AUTHORIZE stage (in
`tests/cordis/test_capabilities.py`); the test in
`tests/cordis/test_pipeline_guard.py` exercises the full
end-to-end pipeline.

Stages:

1. **INGEST** — bytes come in. The pipeline accepts `bytes | str`
   at the entry. No filter; the user input is lossless.
2. **AUTHORIZE** — the capability guard. A capability is required
   at the route level (`@requires(Capability.X)`); the runtime
   guard rejects requests whose capability is not in
   `ACTION_WHITELIST` or whose actor tier is not authorized.
3. **VALIDATE** — schema check. The caller passes a validator
   function that returns the validated payload or raises
   `ValidationError`.
4. **EXECUTE** — the route's domain logic. The caller passes a
   handler that returns a result. Domain errors (existing
   `ValueError`, `SecretEnvelopeError`, etc.) bubble up.
5. **AUDIT** — the C2 SessionEventLog emit. The payload is
   rendered with the 62-code projection via `render_audit_text`
   and appended to the audit log. The in-memory payload is
   unchanged.

The pipeline is intentionally tiny: 5 stages, 5 typed errors,
one path. A future maintainer who needs a new stage adds it
with a new typed error; the test suite's "exhaustive" property
is preserved by iterating the policy data.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Union

from dhc.cordis.capabilities import (
    ACTION_WHITELIST,
    Capability,
    CapabilityDenied,
    _authorize,
)
from dhc.cordis.number_gate import render_audit_text


class NumberGateError(Exception):
    """Raised by the INGEST stage when input is not bytes/str."""


class ValidationError(Exception):
    """Raised by the VALIDATE stage when the payload fails schema."""


class ExecutionError(Exception):
    """Raised by the EXECUTE stage when the handler raises."""


class AuditError(Exception):
    """Raised by the AUDIT stage when the audit log emit fails."""


# The audit log is a simple append-only list of (capability,
# audit_text) tuples. The real C1 service uses the C2
# SessionEventLog; the pipeline uses a list for testability.
_AUDIT_LOG: list[dict[str, Any]] = []


def reset_audit_log() -> None:
    """Test helper: clear the audit log between tests."""
    _AUDIT_LOG.clear()


def get_audit_log() -> list[dict[str, Any]]:
    """Test helper: read the audit log."""
    return list(_AUDIT_LOG)


def ingest(data: Union[bytes, str, None]) -> bytes:
    """INGEST stage. Bytes in. No filter; the user input is lossless."""
    if data is None:
        raise NumberGateError("ingest: data is None")
    if isinstance(data, str):
        return data.encode("utf-8")
    if isinstance(data, (bytes, bytearray)):
        return bytes(data)
    raise NumberGateError(f"ingest: unsupported type {type(data).__name__}")


def authorize(capability: Capability, actor: Any = None) -> Capability:
    """AUTHORIZE stage. The runtime guard returns the Capability
    if authorized or raises `CapabilityDenied`.
    """
    return _authorize(capability.value, actor=actor)


def validate(payload: bytes, validator: Callable[[bytes], Any]) -> Any:
    """VALIDATE stage. The validator returns the validated payload
    or raises `ValidationError` (or any subclass).
    """
    try:
        return validator(payload)
    except Exception as e:
        if isinstance(e, ValidationError):
            raise
        raise ValidationError(f"validate: {e}") from e


async def execute(
    validated: Any,
    handler: Callable[[Any], Awaitable[Any]],
) -> Any:
    """EXECUTE stage. The handler returns a result or raises."""
    try:
        return await handler(validated)
    except Exception as e:
        # Domain errors bubble up; the pipeline does not wrap them.
        raise


def audit(
    capability: Capability,
    payload: Any,
    result: Any = None,
) -> None:
    """AUDIT stage. Append to the audit log with the 62-code
    projection. The in-memory payload is unchanged.
    """
    try:
        # Render the payload via the audit-render filter. The
        # `render_audit_text` function handles bytes and str.
        if isinstance(payload, (bytes, bytearray)):
            audit_text = render_audit_text(bytes(payload))
        elif isinstance(payload, str):
            audit_text = render_audit_text(payload)
        else:
            # Non-bytes/str payloads (e.g. dict, list, int) are
            # JSON-serialized losslessly and then projected.
            import json
            audit_text = render_audit_text(json.dumps(payload, default=str))
        _AUDIT_LOG.append(
            {
                "capability": capability.value,
                "audit_text": audit_text,
                "result": "ok" if result is not None else "none",
            }
        )
    except Exception as e:
        raise AuditError(f"audit: {e}") from e


async def run_pipeline(
    data: Union[bytes, str, None],
    capability: Capability,
    validator: Callable[[bytes], Any],
    handler: Callable[[Any], Awaitable[Any]],
    actor: Any = None,
) -> Any:
    """End-to-end pipeline. The 5 stages in order.

    Errors are typed and surface to the caller:
    - `NumberGateError` (INGEST)
    - `CapabilityDenied` (AUTHORIZE)
    - `ValidationError` (VALIDATE)
    - `ExecutionError` (EXECUTE)
    - `AuditError` (AUDIT)
    """
    payload = ingest(data)
    authorize(capability, actor=actor)
    validated = validate(payload, validator)
    result = await execute(validated, handler)
    audit(capability, payload, result=result)
    return result


__all__ = [
    "NumberGateError",
    "ValidationError",
    "ExecutionError",
    "AuditError",
    "reset_audit_log",
    "get_audit_log",
    "ingest",
    "authorize",
    "validate",
    "execute",
    "audit",
    "run_pipeline",
]
