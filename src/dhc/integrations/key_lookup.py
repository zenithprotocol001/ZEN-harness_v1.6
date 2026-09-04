"""dhc.integrations.key_lookup: per-model API key resolution (ADR-0007 v1.3.2).

Resolves the API key for a `(provider, model_id)` pair by walking
the v1.3.2 lookup chain:

  1. `llm_provider_{provider}_{model_id}` (the canonical key for that
     model — this is what the v1.3.2 Settings UI saves by default).
  2. `llm_provider_{provider}_{model_id}___2`, then `___3`, ... (a
     user with multiple accounts for the same model).
  3. `llm_provider_{provider}_{first_model_id}` (the v1.3.1
     per-provider fallback for users who only set one key per
     provider).

`None` is returned when no key resolves. Callers should raise
`ProviderError(status=401)` to surface the missing key.

The `secrets_service` argument is duck-typed (`get(name) -> str | None`).
This keeps the helper import-free of the cordis layer so it can be
unit-tested with a simple in-memory dict-like object.
"""
from __future__ import annotations

import re
from typing import Callable, Optional, Sequence

# Names that look like `llm_provider_<provider>_<modelPart>`,
# optionally with a `___<n>` disambiguator.
_KEY_NAME_RE = re.compile(r"^llm_provider_([a-z0-9-]+)_([a-z0-9._/-]+?)(?:___(\d+))?$")

# A `get(name) -> str | None` callable. Allows the helper to be used
# against any backend (SecretsService, an in-memory dict, etc.).
SecretsGetter = Callable[[str], Optional[str]]


def default_key_name(provider: str, model_id: str) -> str:
    """The canonical key name for a `(provider, model_id)` pair
    (the no-suffix form). `model_id` is the canonical id with the
    provider prefix stripped."""
    return f"llm_provider_{provider}_{model_id}"


def additional_key_name(provider: str, model_id: str, n: int) -> str:
    """The `___{n}` form of the key name. `n >= 2`."""
    if n < 2:
        raise ValueError(f"n must be >= 2, got {n}")
    return f"{default_key_name(provider, model_id)}___{n}"


def provider_fallback_key_name(
    provider: str, first_model_id: str
) -> str:
    """The v1.3.1 per-provider fallback name. Equivalent to
    `default_key_name(provider, first_model_id)` for the first
    model of the provider."""
    return default_key_name(provider, first_model_id)


def parse_key_name(name: str) -> tuple[str, str, int] | None:
    """Parse a `llm_provider_*` key name into `(provider, model_id, n)`
    where `n == 1` for the canonical form and `n >= 2` for additional
    keys. Returns `None` for names that don't match the pattern."""
    m = _KEY_NAME_RE.match(name)
    if not m:
        return None
    provider, model_part, n_str = m.group(1), m.group(2), m.group(3)
    n = int(n_str) if n_str else 1
    return provider, model_part, n


def lookup_api_key(
    provider: str,
    model_id: str,
    secrets_service: SecretsGetter,
    *,
    provider_first_model_id: str | None = None,
    max_additional: int = 32,
) -> str | None:
    """Resolve the API key for `(provider, model_id)`.

    `provider_first_model_id` is the canonical id (with the provider
    prefix) of the first model for this provider. It is the
    v1.3.1 per-provider fallback. Pass `None` to disable the
    fallback (callers in test setups without a registry should set
    this to `model_id` if they want the same-model default to be
    tried, or `None` to opt out).

    `max_additional` caps the `___N` walk so a malicious secrets
    log with `___99999` doesn't cause a long scan. The default
    of 32 matches the "few extra accounts" use case.

    Returns the API key string, or `None` if no key resolves.
    """
    canonical = default_key_name(provider, model_id)
    value = secrets_service(canonical)
    if value:
        return value
    # Walk ___2, ___3, ... up to max_additional.
    for n in range(2, max_additional + 1):
        v = secrets_service(additional_key_name(provider, model_id, n))
        if v:
            return v
    # v1.3.1 per-provider fallback. Only used if the first-model
    # canonical name is not the same as the canonical we already tried
    # (avoids a redundant second lookup).
    if (
        provider_first_model_id
        and provider_first_model_id != model_id
    ):
        fallback = provider_fallback_key_name(provider, provider_first_model_id)
        return secrets_service(fallback)
    return None


__all__ = [
    "SecretsGetter",
    "default_key_name",
    "additional_key_name",
    "provider_fallback_key_name",
    "parse_key_name",
    "lookup_api_key",
]
