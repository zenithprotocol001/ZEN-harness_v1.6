"""dhc.services.provider_state: kernel-level cache of (configuration
presence, runtime health) for LLM providers.

v1.6.0 (Phase 0, settings subsystem hardening). Written by
`GET /api/llm/health/{provider}` after a probe completes; read by
`ChatPanel` on mount for an instant health badge (no redundant
network calls).

Bounded-entropy principle: `HealthStatus` is a closed enum; new
statuses require a kernel ADR. The cache is per-process; the next
probe rebuilds it after a restart.

The cache is intentionally NOT persisted to disk. It is
information about a *live* upstream provider; persisting it would
risk serving a stale `valid` badge to a user whose key was
revoked while the server was down.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from enum import Enum

from dhc.cordis.secrets import SecretSourceType


class HealthStatus(str, Enum):
    """Closed enum of v1.6.0 provider health statuses."""

    unknown = "unknown"
    valid = "valid"
    invalid = "invalid"
    network_error = "network_error"
    not_supported = "not_supported"


@dataclass
class ProviderState:
    """Snapshot of a single provider's (configuration, health)."""

    present: bool = False
    source: SecretSourceType = SecretSourceType.raw
    health_status: HealthStatus = HealthStatus.unknown
    health_label: str | None = None
    last_check_at: float = 0.0


class ProviderStateManager:
    """Thread-safe per-process cache of `ProviderState` per provider.

    The `update` method is called by the C7 health route after a
    probe completes. The `get` method is called by `ChatPanel`
    on mount. The `snapshot` method returns the entire cache as
    a dict for the `GET /api/settings/state` endpoint.
    """

    def __init__(self) -> None:
        self._states: dict[str, ProviderState] = {}
        self._lock = threading.Lock()

    def update(
        self,
        provider: str,
        *,
        present: bool,
        source: SecretSourceType,
        health: HealthStatus,
        label: str | None = None,
    ) -> None:
        with self._lock:
            self._states[provider] = ProviderState(
                present=present,
                source=source,
                health_status=health,
                health_label=label,
                last_check_at=time.time(),
            )

    def get(self, provider: str) -> ProviderState:
        with self._lock:
            return self._states.get(provider, ProviderState())

    def snapshot(self) -> dict[str, ProviderState]:
        with self._lock:
            return dict(self._states)

    def clear(self, provider: str) -> None:
        with self._lock:
            self._states.pop(provider, None)


__all__ = ["HealthStatus", "ProviderState", "ProviderStateManager"]
