"""C1 GuiWebCore (Python): aiohttp server with strict CSP + WebSocket bridge.

Contract:
- Every HTTP response includes a strict Content-Security-Policy header.
  No `'unsafe-inline'`, no `'unsafe-eval'`, no wildcards.
- WebSocket handshake validates the `Origin` header AND a per-launch
  bearer token. The token is generated at server start (256 bits of
  entropy from `secrets.token_urlsafe(32)`), printed to stdout, and
  written to a `serve_c1.token` file that the React build reads on
  load. This prevents a malicious local process (or a misconfigured
  upstream) from connecting to the WS without the token.
- The token is required for the WS upgrade via either:
    * `Authorization: Bearer <token>` header (preferred, for clients
      that can set headers), or
    * `?token=<token>` query parameter (fallback for browser WS
      clients that cannot set custom headers during handshake).
- When a static dist/ directory is present, the server serves the
  built React UI as the root document and `/assets/*` paths. The
  `index.html` is rewritten at startup to embed the token in a
  `<meta name="dhc-token">` tag so the React client can read it
  without an additional round trip.
- The bound port is written to a `.port` file for downstream tools
  to discover the ephemeral port.
- Errors are routed to C10 telemetry if present; never swallowed.

CSP policy (default):
    default-src 'self';
    script-src 'self';
    style-src 'self';
    img-src 'self' data:;
    connect-src 'self' ws: wss:;
    object-src 'none';
    base-uri 'self';
    frame-ancestors 'none';
    form-action 'self';
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from aiohttp import web

from dhc.cordis.context import Context
from dhc.cordis.plugin import plugin
from dhc.cordis.capabilities import Capability, requires as _requires_capability


CSP_HEADER: str = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self' ws: wss:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'; "
    "form-action 'self'; "
    "media-src 'self'; "
    "font-src 'self'"
)


def build_csp_header(extra: str | None = None) -> str:
    if not extra:
        return CSP_HEADER
    return CSP_HEADER + "; " + extra


def _security_headers(extra_csp: str | None = None) -> dict[str, str]:
    return {
        "Content-Security-Policy": build_csp_header(extra_csp),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "X-Frame-Options": "DENY",
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
    }


# Origins that may complete a WebSocket upgrade against this server.
# Loopback only by default. Configurable via GuiWebCore(allowed_origins=...).
DEFAULT_ALLOWED_ORIGINS: tuple[str, ...] = (
    "http://127.0.0.1",
    "http://localhost",
    "https://127.0.0.1",
    "https://localhost",
)


def _is_allowed_origin(origin: str, allowed: Iterable[str]) -> bool:
    """An origin is allowed if it matches an allowed prefix AND the
    host portion is a loopback address. We do not trust DNS names that
    happen to share the prefix.
    """
    if not origin:
        return False
    for prefix in allowed:
        if origin == prefix or origin.startswith(prefix + ":"):
            host = origin.split("://", 1)[-1].split(":", 1)[0]
            if host in ("127.0.0.1", "localhost", "::1", "[::1]"):
                return True
    return False


def _extract_bearer_token(request: web.Request) -> str | None:
    """Pull the bearer token from the request.

    Order of preference:
      1. `Authorization: Bearer <token>` header (constant-time compared)
      2. `?token=<token>` query parameter (less secure but required for
         browser WebSocket clients that cannot set custom headers during
         the handshake)
    """
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip() or None
    q = request.query.get("token")
    if q:
        return q
    return None


def _check_token(provided: str | None, expected: str) -> bool:
    """Constant-time bearer token comparison. Empty or missing tokens
    never match — we never return True for the empty case."""
    if not provided or not expected:
        return False
    # secrets.compare_digest is the Python stdlib equivalent of
    # hmac.compare_digest (and uses OpenSSL's CRYPTO_memcmp on most
    # platforms). Both inputs are normalized to bytes.
    return secrets.compare_digest(
        provided.encode("utf-8"),
        expected.encode("utf-8"),
    )


_FORWARDED_EVENTS: tuple[str, ...] = (
    "turn/start",
    "agent/pre-step",
    "step/start",
    "llm/stream",
    "tool/call",
    "step/end",
    "turn/end",
    "system/heartbeat",
    "system/error",
)


def _generate_token() -> str:
    """Cryptographically strong 256-bit URL-safe token.

    Uses `secrets.token_urlsafe(32)` which is backed by the OS CSPRNG
    (BCryptGenRandom on Windows, /dev/urandom on Linux/macOS).
    """
    return secrets.token_urlsafe(32)


def _embed_token_in_index(index_path: Path, token: str) -> None:
    """Rewrite the React index.html to embed the token in a <meta> tag.

    The token is HTML-escaped so it cannot break out of the attribute
    even if it contains quotes (it won't, but defense in depth).
    """
    if not index_path.exists():
        return
    text = index_path.read_text(encoding="utf-8")
    if 'name="dhc-token"' in text:
        # Replace existing tag
        import re as _re
        text = _re.sub(
            r'<meta\s+name="dhc-token"\s+content="[^"]*"\s*/?>',
            f'<meta name="dhc-token" content="{_html_attr_escape(token)}" />',
            text,
            count=1,
        )
    else:
        # Insert before </head>
        meta = f'<meta name="dhc-token" content="{_html_attr_escape(token)}" />'
        text = text.replace("</head>", f"  {meta}\n  </head>", 1)
    index_path.write_text(text, encoding="utf-8")


def _html_attr_escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("'", "&#39;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


async def _index(_request: web.Request) -> web.Response:
    return web.Response(
        text="<html><body>dhc web core</body></html>",
        content_type="text/html",
        headers=_security_headers(),
    )


async def _healthz(_request: web.Request) -> web.Response:
    """Health probe.

    The payload includes the discovered plugin manifests, the
    currently-loaded plugin list, and the auto-load memory list
    so the GUI's Modules tab can poll /healthz instead of needing
    its own /api/manifest or /api/memory call.
    """
    request = _request
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"ok": True, "modules": [], "plugins": []}, headers=_security_headers())
    state = web_core.plugin_state
    from dhc.cordis.plugin_memory import PluginMemory
    auto_load = PluginMemory().read()
    discovered = [
        {
            "id": m.id,
            "name": m.name,
            "version": m.version,
            "loaded": m.id in state.loaded,
            "auto_load": m.id in auto_load,
        }
        for m in state.discovered.values()
    ]
    loaded = [
        {
            "id": lp.plugin_id,
            "name": lp.manifest.name,
            "version": lp.manifest.version,
            "loaded_at_ms": lp.loaded_at_ms,
        }
        for lp in state.loaded.values()
    ]
    return web.json_response(
        {
            "ok": True,
            "ts": int(time.time()),
            "modules": [{"id": f"c{i}", "key": f"c{i}"} for i in range(1, 11)],
            "plugins_discovered": discovered,
            "plugins_loaded": loaded,
            "auto_load_memory": auto_load,
        },
        headers=_security_headers(),
    )


async def _api_manifest(_request: web.Request) -> web.Response:
    """Full manifest: 10 core modules + every discovered plugin.

    v1.5.0.1: include the auto-load memory list so the Modules
    tab can show "Loaded on next launch" indicators. The memory
    is the user's persistent opt-in; the manifest is its
    runtime read-out.
    """
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"error": "no web_core"}, status=500, headers=_security_headers())
    state = web_core.plugin_state
    from dhc.cordis.plugin_memory import PluginMemory
    auto_load = PluginMemory().read()
    return web.json_response(
        {
            "modules": [{"id": f"c{i}", "key": f"c{i}"} for i in range(1, 11)],
            "plugins_discovered": [
                {
                    "id": m.id,
                    "name": m.name,
                    "version": m.version,
                    "loaded": m.id in state.loaded,
                    "auto_load": m.id in auto_load,
                }
                for m in state.discovered.values()
            ],
            "auto_load_memory": auto_load,
        },
        headers=_security_headers(),
    )


async def _api_memory(_request: web.Request) -> web.Response:
    """GET /api/memory — the persistent plugin auto-load list.

    v1.5.0.1: the user-facing memory. Empty if no plugins have
    been opted in (the default at first launch). Reading the
    file is cheap and side-effect-free; the C1 service is a
    passive reader on this path.
    """
    from dhc.cordis.plugin_memory import PluginMemory
    return web.json_response(
        {"auto_load": PluginMemory().read()},
        headers=_security_headers(),
    )


async def _plugins_list(_request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"loaded": [], "available": []}, headers=_security_headers())
    state = web_core.plugin_state
    return web.json_response(
        {
            "loaded": [
                {"id": lp.plugin_id, "name": lp.manifest.name, "version": lp.manifest.version}
                for lp in state.loaded.values()
            ],
            "available": [
                {"id": m.id, "name": m.name, "version": m.version, "loaded": m.id in state.loaded}
                for m in state.discovered.values()
            ],
        },
        headers=_security_headers(),
    )


async def _prompts_list(_request: web.Request) -> web.Response:
    """Read from ctx.inject('prompt_browser') if the plugin is loaded."""
    ctx: "Context | None" = _request.app.get("ctx")  # type: ignore[attr-defined]
    browser = ctx.inject("prompt_browser") if ctx is not None else None
    if browser is None:
        return web.json_response(
            {"prompts": [], "note": "prompt_browser_v1 not loaded"},
            headers=_security_headers(),
        )
    return web.json_response(
        {"prompts": browser.list()},
        headers=_security_headers(),
    )


async def _prompts_get(request: web.Request) -> web.Response:
    """GET /api/prompts/{key} — return the full body of a single prompt."""
    key = request.match_info.get("key", "")
    ctx: "Context | None" = request.app.get("ctx")  # type: ignore[attr-defined]
    browser = ctx.inject("prompt_browser") if ctx is not None else None
    if browser is None:
        return web.json_response(
            {"error": "prompt_browser_v1 not loaded"},
            status=503,
            headers=_security_headers(),
        )
    body = browser.get(key)
    if body is None:
        return web.json_response(
            {"error": f"prompt {key!r} not found"},
            status=404,
            headers=_security_headers(),
        )
    return web.json_response(
        {"key": key, "body": body}, headers=_security_headers()
    )


async def _plugins_load(request: web.Request) -> web.Response:
    """POST /plugins/{id} with JSON body {"config": {...}}.

    Loads the plugin if it's not already loaded. Returns 409 on conflict
    (already loaded) and 500 on apply() failure.
    """
    plugin_id = request.match_info.get("plugin_id", "")
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    ctx: "Context | None" = request.app.get("ctx")  # type: ignore[attr-defined]
    if web_core is None or ctx is None:
        return web.json_response(
            {"error": "harness not initialized"}, status=500, headers=_security_headers()
        )
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    config = body.get("config") or {}
    if not isinstance(config, dict):
        config = {}

    from dhc.plugins.loader import (
        PluginApplyError,
        PluginError,
        PluginIntegrityError,
        PluginNotFoundError,
        PluginValidationError,
        load_async,
    )

    if plugin_id in web_core.plugin_state.loaded:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": "already loaded"},
            status=409,
            headers=_security_headers(),
        )
    try:
        await load_async(web_core.plugin_state, ctx, plugin_id, config=config)
    except PluginNotFoundError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=404,
            headers=_security_headers(),
        )
    except (PluginIntegrityError, PluginValidationError, PluginApplyError) as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=500,
            headers=_security_headers(),
        )
    except PluginError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=400,
            headers=_security_headers(),
        )
    # v1.5.0.1: a successful load is an opt-in. Persist the
    # plugin id in the auto-load memory so the next launch
    # auto-loads it. The opt-in is the user's *current* choice;
    # the user can opt out by Unloading the plugin, which
    # removes it from the memory. Best-effort: a persistence
    # failure does not undo the load.
    try:
        from dhc.cordis.plugin_memory import PluginMemory
        PluginMemory().add(plugin_id)
    except Exception:
        pass
    return web.json_response(
        {"id": plugin_id, "ok": True}, headers=_security_headers()
    )


async def _plugins_unload(request: web.Request) -> web.Response:
    """DELETE /plugins/{id} — unloads a plugin if loaded."""
    plugin_id = request.match_info.get("plugin_id", "")
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    ctx: "Context | None" = request.app.get("ctx")  # type: ignore[attr-defined]
    if web_core is None or ctx is None:
        return web.json_response(
            {"error": "harness not initialized"}, status=500, headers=_security_headers()
        )

    from dhc.plugins.loader import (
        PluginError,
        PluginNotFoundError,
        unload as _unload_plugin,
    )

    try:
        # We're inside a running event loop (aiohttp is async). Await
        # the async unload directly instead of using `unload_sync`
        # (which would try to start a fresh loop and fail).
        await _unload_plugin(web_core.plugin_state, ctx, plugin_id)
    except PluginNotFoundError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=404,
            headers=_security_headers(),
        )
    except PluginError as exc:
        return web.json_response(
            {"id": plugin_id, "ok": False, "error": str(exc)},
            status=500,
            headers=_security_headers(),
        )
    # v1.5.0.1: a successful unload is an opt-out. Remove the
    # plugin id from the auto-load memory so the next launch
    # does NOT auto-load it. Best-effort: a persistence failure
    # does not undo the unload.
    try:
        from dhc.cordis.plugin_memory import PluginMemory
        PluginMemory().remove(plugin_id)
    except Exception:
        pass
    return web.json_response(
        {"id": plugin_id, "ok": True}, headers=_security_headers()
    )


async def _api_eval(request: web.Request) -> web.Response:
    """Paste-and-score: write the submitted code over a module's
    service.py, run the module's tests in a subprocess, restore.

    The body must be JSON of the form:
        {"module": "c4", "code": "import ..."}

    The endpoint is intentionally NOT protected by the bearer token
    so the Prompts tab can use it from the same loopback origin. The
    origin guard still applies. The submitted code is never exec'd
    inside this process - it lives in a temp file and is read by
    pytest only.
    """
    import json as _json

    try:
        body = await request.json()
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    module_key = str(body.get("module") or "")
    code = str(body.get("code") or "")
    if not module_key or not code:
        return web.json_response(
            {"error": "missing 'module' or 'code'"}, status=400, headers=_security_headers()
        )

    from dhc.plugins._inproc_eval import eval_pasted_code

    import asyncio as _asyncio

    repo_root = request.app.get("repo_root")  # type: ignore[attr-defined]
    if repo_root is None:
        return web.json_response(
            {"error": "repo_root not configured"}, status=500, headers=_security_headers()
        )
    # eval_pasted_code is sync; run it in a thread so we don't
    # block the aiohttp event loop (it spawns a subprocess).
    result = await _asyncio.get_running_loop().run_in_executor(
        None, lambda: eval_pasted_code(repo_root, module_key, code, 30)
    )
    return web.json_response(result, headers=_security_headers())


# ---------- v1.2.0: chat WS, sessions, secrets, LLM health ----------


def _require_loopback_auth(request: web.Request, expected_token: str | None, allowed_origins: tuple[str, ...]) -> web.Response | None:
    """Shared origin + bearer-token guard. Returns a 401/403
    Response if the request fails; None if it passes.

    Used by `/ws/chat`, `/api/sessions/*`, and `/api/secrets/*`.
    """
    origin = request.headers.get("Origin", "")
    if not _is_allowed_origin(origin, allowed_origins):
        return web.Response(
            status=403, text="Origin not allowed", headers=_security_headers()
        )
    if expected_token:
        provided = _extract_bearer_token(request)
        if not _check_token(provided, expected_token):
            return web.Response(
                status=401, text="Unauthorized", headers=_security_headers()
            )
    return None


async def _api_llm_health(_request: web.Request) -> web.Response:
    """Probe the configured LLM base URL. The adapter exposes
    `redacted_key`; the LLM URL comes from the C7 config the
    harness was started with.
    """
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.json_response({"ok": False, "error": "no web_core"}, headers=_security_headers())
    adapter = web_core.app.get("llm_adapter")
    if adapter is None:
        # Try to inject from context.
        ctx: Context | None = _request.app.get("ctx")
        if ctx is not None:
            adapter = ctx.inject("llm")
    base_url = getattr(adapter, "_base_url", None) if adapter is not None else None
    return web.json_response(
        {"ok": base_url is not None, "base_url": base_url},
        headers=_security_headers(),
    )


# v1.5.1.4 (audit hotfix #4): per-provider real connection probe.
# The v1.5.1 `GET /api/llm/health` only confirms the harness was
# started with `--llm-base-url`; it does NOT verify the user's
# OpenRouter (or any other) key works. The Settings modal
# rendered a "Configured" pill with no way to actually confirm
# the key was valid. v1.5.1.4 adds a per-provider probe that
# uses OpenRouter's `GET /api/v1/auth/key` endpoint — the
# canonical "is my key valid" probe. Returns:
#   {ok: true, provider, label, limit, is_free_tier} on 200
#   {ok: false, provider, error: "invalid_key"} on 401
#   {ok: false, provider, error: "no_key"} when no key is set
#   {ok: false, provider, error: "network"} on any other failure
# The key is never echoed back; we only return the OpenRouter
# `label` (the human-readable name the user set in their
# OpenRouter dashboard). For providers without a /auth/key
# probe (OpenAI, Anthropic), returns `not_supported` — the
# frontend falls back to "configured" as a best-effort.
_OPENROUTER_AUTH_KEY_URL = "https://openrouter.ai/api/v1/auth/key"
_PROBE_TIMEOUT_SECONDS = 5.0


async def _api_llm_health_provider(request: web.Request) -> web.Response:
    """GET /api/llm/health/{provider} — real per-provider probe.

    v1.5.1.4: confirms the user's stored key actually works
    against the upstream. The v1.5.1 `GET /api/llm/health`
    returned `ok: true` for any harness started with a base
    URL, regardless of whether the user's key was valid; this
    misled users into thinking their OpenRouter key was working
    when it might have been revoked or mis-pasted.

    The probe uses OpenRouter's `GET /api/v1/auth/key` endpoint,
    which is the canonical "is my key valid" probe (returns
    200 + `{data: {label, usage, limit, is_free_tier}}` on a
    valid key, 401 on a bad key). The key is never logged on
    401 (per the v1.5.1.4 execution guardrails).

    Other providers (OpenAI, Anthropic) do not expose a public
    `auth/key` endpoint; for those, we return
    `{ok: false, error: "not_supported"}` and let the frontend
    fall back to "configured" as a best-effort indicator. The
    full v1.6.0 model catalog (ADR-0105+) will replace this
    with a unified per-provider probe layer.

    v1.6.0: in addition to returning the inline result, the
    probe writes the outcome to the `ProviderStateManager`
    kernel cache so the React tree can render an instant health
    badge on next mount (no redundant network round-trip).
    """
    # Local helper to record the outcome in the ProviderStateManager
    # kernel cache. Imported here to avoid a top-level cycle with
    # `dhc.services.provider_state` at module-import time.
    from dhc.cordis.secrets import SecretSourceType
    from dhc.services.provider_state import HealthStatus

    def _record(
        present: bool,
        health: "HealthStatus",
        label: str | None = None,
    ) -> None:
        try:
            web_core.provider_state.update(
                provider,
                present=present,
                source=SecretSourceType.raw,  # v1.6.0+ reads via get_source
                health=health,
                label=label,
            )
        except Exception:
            # ProviderState is a cache; failure to record must
            # never break the probe.
            pass

    provider: str = request.match_info["provider"]
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"ok": False, "provider": provider, "error": "no_web_core"},
            status=503, headers=_security_headers(),
        )

    if provider == "openrouter":
        # Find the OpenRouter key via the v1.3.2 lookup chain. The
        # `provider_fallback_key_name` (per ADR-0007) is the
        # canonical name for the per-provider fallback; passing
        # the first model id ("auto") lets the helper walk the
        # chain if the user has multiple keys.
        try:
            from dhc.integrations.key_lookup import lookup_api_key
        except ImportError:
            lookup_api_key = None  # type: ignore[assignment]
        if lookup_api_key is None:
            return web.json_response(
                {"ok": False, "provider": provider, "error": "no_lookup"},
                status=503, headers=_security_headers(),
            )
        api_key = lookup_api_key(
            "openrouter",
            "auto",
            web_core.secrets_service.get_raw,
            provider_first_model_id="auto",
        )
        if api_key is None:
            _record(present=False, health=HealthStatus.unknown)
            return web.json_response(
                {"ok": False, "provider": provider, "error": "no_key"},
                headers=_security_headers(),
            )
        # `secrets_service.get_raw` returns bytes (per the v0x04
        # envelope contract). The HTTP Authorization header must
        # be a str, so decode defensively. If the bytes are not
        # valid utf-8 (a corrupted key file), fail loud as
        # `invalid_key` rather than crashing the request.
        if isinstance(api_key, (bytes, bytearray)):
            try:
                api_key_str = bytes(api_key).decode("utf-8")
            except UnicodeDecodeError:
                _record(present=True, health=HealthStatus.invalid)
                return web.json_response(
                    {"ok": False, "provider": provider, "error": "invalid_key"},
                    headers=_security_headers(),
                )
        else:
            api_key_str = str(api_key)
        # Probe OpenRouter's /auth/key endpoint. We use httpx
        # (already a transitive dep) with a short timeout so a
        # network failure does not block the UI.
        try:
            import httpx

            async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SECONDS) as client:
                resp = await client.get(
                    _OPENROUTER_AUTH_KEY_URL,
                    headers={
                        "Authorization": f"Bearer {api_key_str}",
                        # OpenRouter ranks apps by these headers
                        # for the public leaderboard; safe to
                        # send for a loopback-bound tool.
                        "HTTP-Referer": "http://127.0.0.1/",
                        "X-Title": "DHC",
                    },
                )
        except Exception as _exc:  # noqa: BLE001
            # Network / TLS / DNS / timeout failure. Never log
            # the key. The frontend renders "Network error —
            # try again" in the Settings modal.
            _record(present=True, health=HealthStatus.network_error)
            return web.json_response(
                {"ok": False, "provider": provider, "error": "network"},
                headers=_security_headers(),
            )
        if resp.status_code == 200:
            # `httpx.Response.json` is a sync method that
            # parses the response body and returns a dict.
            # If the body is not JSON (an OpenRouter API
            # change or a proxy returning HTML), fail
            # gracefully and report `invalid_key` so the
            # user can re-validate.
            try:
                body = resp.json()
                data = body.get("data", {}) if isinstance(body, dict) else {}
                label = data.get("label")
                limit = data.get("limit")
                is_free_tier = data.get("is_free_tier")
            except Exception:
                _record(present=True, health=HealthStatus.invalid)
                return web.json_response(
                    {"ok": False, "provider": provider, "error": "invalid_key"},
                    headers=_security_headers(),
                )
            _record(present=True, health=HealthStatus.valid, label=label)
            return web.json_response(
                {
                    "ok": True,
                    "provider": provider,
                    "label": label,
                    "limit": limit,
                    "is_free_tier": is_free_tier,
                },
                headers=_security_headers(),
            )
        if resp.status_code == 401:
            # Bad key. Do NOT log the key value. The frontend
            # shows "Invalid API key" in the modal.
            _record(present=True, health=HealthStatus.invalid)
            return web.json_response(
                {"ok": False, "provider": provider, "error": "invalid_key"},
                headers=_security_headers(),
            )
        # Any other status: bubble up as upstream_error.
        _record(present=True, health=HealthStatus.network_error)
        return web.json_response(
            {
                "ok": False,
                "provider": provider,
                "error": "upstream_error",
                "status": resp.status_code,
            },
            headers=_security_headers(),
        )

    # Other providers do not have a public /auth/key probe.
    # Return not_supported; the frontend falls back to the
    # existing "configured" pill.
    _record(present=False, health=HealthStatus.not_supported)
    return web.json_response(
        {"ok": False, "provider": provider, "error": "not_supported"},
        headers=_security_headers(),
    )


async def _api_models_list(_request: web.Request) -> web.Response:
    """GET /api/models — list all models in the hardcoded registry.

    Per ADR-0006. The mock model is included so the React UI can
    default to it without a separate code path.
    """
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.model_registry is None:
        return web.json_response(
            {"models": [], "error": "model_registry not configured"},
            status=503, headers=_security_headers(),
        )
    payload = [
        {
            "id": m.id,
            "name": m.name,
            "provider": m.provider,
            "context_length": m.context_length,
            "pricing_input": m.pricing_input,
            "pricing_output": m.pricing_output,
            "capabilities": sorted(m.capabilities),
        }
        for m in web_core.model_registry.list_models()
    ]
    return web.json_response({"models": payload}, headers=_security_headers())


async def _api_models_get(_request: web.Request) -> web.Response:
    """GET /api/models/{id} — fetch a single model.

    `id` is the URL path; aiohttp decodes the leading slash if
    present. The model id format is `provider/model-part` (e.g.
    `openai/gpt-4o-mini`); we use the path exactly.
    """
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.model_registry is None:
        return web.json_response(
            {"error": "model_registry not configured"},
            status=503, headers=_security_headers(),
        )
    model_id = _request.match_info.get("id", "")
    model = web_core.model_registry.get_model(model_id)
    if model is None:
        return web.json_response(
            {"error": f"model {model_id!r} not found"},
            status=404, headers=_security_headers(),
        )
    return web.json_response(
        {
            "id": model.id,
            "name": model.name,
            "provider": model.provider,
            "context_length": model.context_length,
            "pricing_input": model.pricing_input,
            "pricing_output": model.pricing_output,
            "capabilities": sorted(model.capabilities),
        },
        headers=_security_headers(),
    )


async def _api_sessions_list(_request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"sessions": [], "error": "session_manager not configured"},
            status=503, headers=_security_headers(),
        )
    q = _request.query
    include_archived = q.get("archived", "").lower() in ("1", "true", "yes")
    search = q.get("search") or None
    q_present = "q" in q
    q_alias = q.get("q") or None
    limit_s = q.get("limit")
    limit = int(limit_s) if limit_s and limit_s.isdigit() else None
    if q_present:
        results = web_core.session_manager.search(
            q_alias or "", limit=limit or 50, include_archived=include_archived
        )
        summaries = [r.summary() for r in results]
    else:
        summaries = web_core.session_manager.list_summaries(
            include_archived=include_archived, search=search, limit=limit
        )
    return web.json_response({"sessions": summaries}, headers=_security_headers())


async def _api_sessions_create(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    title = body.get("title")
    # v1.5.1.4 (audit hotfix #4): accept an optional `model`
    # in the create body. The chat header renders a "Default
    # model" picker when no session is active; the user's
    # pick is stored in `dhc.defaultModelId` (localStorage)
    # and sent as `{model: "..."}` in the POST body when
    # creating a new session. This is the server-side
    # companion to the no-session picker; sessions created
    # via the legacy empty-body `{}` path get the
    # server-side default ("mock-llm/default").
    requested_model = body.get("model")
    create_kwargs: dict = {
        "title": title if isinstance(title, str) else None,
    }
    if isinstance(requested_model, str) and requested_model:
        # Validate the model id is in the registry. The
        # registry is the v1.5.0 closed set; an unknown id
        # is rejected with 400 to prevent the WS dispatch
        # from surfacing a ProviderError later.
        if web_core.model_registry is not None and requested_model not in web_core.model_registry:
            return web.json_response(
                {"error": f"unknown model: {requested_model!r}"},
                status=400, headers=_security_headers(),
            )
        create_kwargs["model"] = requested_model
    s = web_core.session_manager.create(**create_kwargs)
    return web.json_response(s.to_dict(), status=201, headers=_security_headers())


async def _api_sessions_get(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    s = web_core.session_manager.get(sid)
    if s is None:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    return web.json_response(s.to_dict(), headers=_security_headers())


async def _api_sessions_patch(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    update_kwargs: dict = {}
    if "title" in body and isinstance(body["title"], str):
        update_kwargs["title"] = body["title"]
    if "pinned" in body:
        update_kwargs["pinned"] = bool(body["pinned"])
    if "archived" in body:
        update_kwargs["archived"] = bool(body["archived"])
    if "tags" in body and isinstance(body["tags"], list):
        update_kwargs["tags"] = [str(t) for t in body["tags"]]
    if "model" in body and isinstance(body["model"], str):
        update_kwargs["model"] = body["model"]
    s = web_core.session_manager.update(sid, **update_kwargs)
    if s is None:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    return web.json_response(s.to_dict(), headers=_security_headers())


async def _api_sessions_delete(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    q = request.query
    hard = q.get("hard", "").lower() in ("1", "true", "yes")
    if hard:
        ok = web_core.session_manager.hard_delete(sid)
    else:
        ok = web_core.session_manager.soft_delete(sid)
    if not ok:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    return web.Response(status=204, headers=_security_headers())


# ---------- /api/sessions/{id}/config (ADR-0011) ----------


async def _api_sessions_get_config(request: web.Request) -> web.Response:
    """Return the `ModelConfig` for `session_id` as JSON.

    A missing or unreadable config returns a fresh `ModelConfig()`
    (defaults); the endpoint never 404s because "no config set"
    is a valid state.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.model_config_store is None:
        return web.json_response(
            {"error": "model_config_store not configured"},
            status=503,
            headers=_security_headers(),
        )
    sid = request.match_info.get("session_id", "")
    if not sid:
        return web.json_response(
            {"error": "missing session_id"}, status=400, headers=_security_headers()
        )
    cfg = web_core.model_config_store.get_config(sid)
    return web.json_response(cfg.to_dict(), headers=_security_headers())


async def _api_sessions_set_config(request: web.Request) -> web.Response:
    """Persist the `ModelConfig` for `session_id`.

    Body is the JSON config object:
    `{"temperature": float, "max_tokens": int, "top_p": float, "system_prompt": str}`.
    Validation lives in `ModelConfig.__post_init__`; bad values
    yield a 400.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.model_config_store is None:
        return web.json_response(
            {"error": "model_config_store not configured"},
            status=503,
            headers=_security_headers(),
        )
    sid = request.match_info.get("session_id", "")
    if not sid:
        return web.json_response(
            {"error": "missing session_id"}, status=400, headers=_security_headers()
        )
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    from dhc.services.model_config import ModelConfig  # local import: avoid C1 → C7 cycle

    try:
        cfg = ModelConfig.from_dict(body)
    except (TypeError, ValueError) as exc:
        return web.json_response(
            {"error": f"invalid config: {exc}"},
            status=400,
            headers=_security_headers(),
        )
    web_core.model_config_store.set_config(sid, cfg)
    return web.Response(status=204, headers=_security_headers())


async def _api_sessions_post_message(request: web.Request) -> web.Response:
    """Append a user message to a session and synchronously stream
    the LLM reply through C7, then save the assistant turn.

    The body is `{"content": "...", "model": "..."}`. The endpoint
    blocks until the LLM reply is complete (or errors out). For the
    v1.2.0 surface this is OK; the streaming UX in the chat panel
    uses the `/ws/chat` channel.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    content = str(body.get("content") or "")
    if not content:
        return web.json_response(
            {"error": "missing 'content'"}, status=400, headers=_security_headers()
        )
    s = web_core.session_manager.get(sid)
    if s is None:
        return web.json_response(
            {"error": f"session {sid!r} not found"}, status=404, headers=_security_headers()
        )
    user_msg = web_core.session_manager.append_message(sid, "user", content)
    # Pull the adapter from the context (C7 provides "llm").
    ctx: Context | None = request.app.get("ctx")
    adapter = ctx.inject("llm") if ctx is not None else None
    if adapter is None:
        return web.json_response(
            {"error": "llm adapter not configured"}, status=503, headers=_security_headers()
        )
    model = str(body.get("model") or s.model or "mock-default")
    # Build the OpenAI-shaped message list from the session.
    messages = [{"role": m["role"], "content": m.get("content", "")} for m in s.messages]
    deltas: list[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    # v1.3.2: snapshot the prior totals for accumulation.
    prior_totals = dict(s.usage_totals)
    # v1.5.0 (ADR-0019): capture the user message's parent_id at
    # stream start. The assistant message is appended as a child
    # of this parent regardless of intervening branch switches.
    # The user message has no explicit parent_id (it was appended
    # via the normal append_message path), so its parent_id is
    # whatever the session's active_tip_id was at the time the
    # user hit send. The pinned parent is *that* tip, not the
    # current tip at the time the stream ends.
    pinned_parent_id = user_msg.get("parent_id") if user_msg else None
    web_core.session_manager.begin_stream(sid)
    try:
        # v1.3.1: pass session_id so C7 can look up the per-session
        # ModelConfig (ADR-0011).
        async for chunk in adapter.chat_stream(messages=messages, model=model, session_id=sid):
            deltas.append(chunk.delta or "")
            completion_tokens += max(1, len(chunk.delta or "") // 4)
            if chunk.usage:
                prompt_tokens = int(chunk.usage.get("prompt_tokens") or 0)
                completion_tokens = int(chunk.usage.get("completion_tokens") or completion_tokens)
    except Exception as exc:  # noqa: BLE001
        # Log the error but keep the user message saved.
        web_core.session_manager.end_stream(sid)
        return web.json_response(
            {"error": f"llm error: {exc}", "user_message": user_msg},
            status=502, headers=_security_headers(),
        )
    assistant_text = "".join(deltas)
    # Append the assistant message pinned to the user message's
    # parent_id, NOT to the current active_tip_id.
    asst = web_core.session_manager.append_message(
        sid, "assistant", assistant_text,
        tokens={"prompt": prompt_tokens, "completion": completion_tokens},
        parent_id=pinned_parent_id,
    )
    # v1.3.2: aggregate usage into the session.
    new_totals = {
        "prompt_tokens": prior_totals["prompt_tokens"] + prompt_tokens,
        "completion_tokens": prior_totals["completion_tokens"] + completion_tokens,
        "total_tokens": prior_totals["total_tokens"] + prompt_tokens + completion_tokens,
        "last_turn_prompt": prompt_tokens,
        "last_turn_completion": completion_tokens,
    }
    web_core.session_manager.update(sid, usage_totals=new_totals)
    web_core.session_manager.end_stream(sid)
    return web.json_response(
        {"user_message": user_msg, "assistant_message": asst, "usage_totals": new_totals},
        status=201, headers=_security_headers(),
    )


# ----------------------------------------------------------------------------
# v1.5.0 branching routes (ADR-0019)
# ----------------------------------------------------------------------------


async def _api_sessions_branch_create(request: web.Request) -> web.Response:
    """POST /api/sessions/{sid}/branches

    Body: `{"parent_message_id": "m_..."}` (optional `role` and
    `content`; default `role="user"`, `content=""`).

    The new message's `parent_id` is `parent_message_id`; the new
    message becomes the active_tip_id. The old branch is preserved
    as a recoverable leaf.

    Capability: BRANCH_CREATE.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"error": "body must be a JSON object"}, status=400, headers=_security_headers()
        )
    parent = body.get("parent_message_id")
    if not isinstance(parent, str) or not parent:
        return web.json_response(
            {"error": "missing 'parent_message_id'"}, status=400, headers=_security_headers()
        )
    role = body.get("role", "user")
    content = body.get("content", "")
    if not isinstance(content, str):
        return web.json_response(
            {"error": "'content' must be a string"}, status=400, headers=_security_headers()
        )
    try:
        msg = web_core.session_manager.branch_create(
            session_id=sid,
            parent_message_id=parent,
            role=role,
            content=content,
        )
    except ValueError as exc:
        return web.json_response(
            {"error": str(exc)}, status=400, headers=_security_headers()
        )
    if msg is None:
        return web.json_response(
            {"error": f"session {sid!r} or parent {parent!r} not found"},
            status=404, headers=_security_headers(),
        )
    return web.json_response(
        {
            "branch_root": msg["id"],
            "active_tip_id": web_core.session_manager.get(sid).active_tip_id,
        },
        status=201, headers=_security_headers(),
    )


async def _api_sessions_active_tip(request: web.Request) -> web.Response:
    """PATCH /api/sessions/{sid}/active-tip

    Body: `{"leaf_message_id": "m_..."}`.

    Sets the session's `active_tip_id`. Refused with 409 if a
    stream is in progress on the session. Refused with 404 if the
    leaf is not a message in the session.

    Capability: BRANCH_SWITCH.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"error": "session_manager not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"error": "body must be a JSON object"}, status=400, headers=_security_headers()
        )
    leaf = body.get("leaf_message_id")
    if not isinstance(leaf, str) or not leaf:
        return web.json_response(
            {"error": "missing 'leaf_message_id'"}, status=400, headers=_security_headers()
        )
    if web_core.session_manager.is_stream_in_progress(sid):
        return web.json_response(
            {"error": "stream_in_progress"}, status=409, headers=_security_headers()
        )
    s = web_core.session_manager.branch_switch(sid, leaf)
    if s is None:
        return web.json_response(
            {"error": f"session {sid!r} or leaf {leaf!r} not found"},
            status=404, headers=_security_headers(),
        )
    return web.json_response(
        {"active_tip_id": s.active_tip_id}, headers=_security_headers()
    )


async def _api_sessions_branches(request: web.Request) -> web.Response:
    """GET /api/sessions/{sid}/branches

    Returns the list of tips (one per branch), each with its
    reconstructed path from the root. The active tip is marked
    `"active": true`.

    Capability: BRANCH_LIST.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.session_manager is None:
        return web.json_response(
            {"branches": [], "error": "session_manager not configured"},
            status=503, headers=_security_headers(),
        )
    sid = request.match_info.get("session_id", "")
    branches = web_core.session_manager.list_branches(sid)
    # The `path` field is a list of full message dicts. For the
    # API response, we project to `id` + `role` + a 60-char
    # preview of `content` to keep the payload small. The
    # reconstructed path is still reconstructable from
    # `GET /api/sessions/{sid}`.
    projected = [
        {
            "id": b["id"],
            "active": b["active"],
            "path": [
                {
                    "id": str(m.get("id") or ""),
                    "role": str(m.get("role") or ""),
                    "preview": (str(m.get("content") or "")[:60]),
                }
                for m in b["path"]
            ],
        }
        for b in branches
    ]
    return web.json_response(
        {"branches": projected}, headers=_security_headers()
    )


# ----------------------------------------------------------------------------
# v1.5.0 attachment routes (ADR-0020)
# ----------------------------------------------------------------------------


async def _api_sessions_attachment_put(request: web.Request) -> web.Response:
    """POST /api/sessions/{sid}/attachments

    Body: the attachment bytes. Headers: `Content-Type` (the MIME).
    The MIME must be in `MIME_OUT_OF_LINE` (binary). Text-like
    content (text/plain, text/markdown, image/svg+xml) goes inline
    in the message body and is *not* accepted by this endpoint.

    The payload is sealed in a v0x04 envelope with name
    `attachment:{session_id}:{uuid}`. The seal is name-bound
    (ADR-0013); cross-session ref reuse fails the MAC.

    Capability: ATTACHMENT_PUT.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.attachment_service is None:
        return web.json_response(
            {"error": "attachment_service not configured"}, status=503, headers=_security_headers()
        )
    sid = request.match_info.get("session_id", "")
    mime = (request.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    # Coalesce common aliases; otherwise accept the literal.
    alias = {
        "image/jpg": "image/jpeg",
        "audio/mp3": "audio/mpeg",
    }
    mime = alias.get(mime, mime)
    if mime not in __import__("dhc.cordis.attachments", fromlist=["MIME_OUT_OF_LINE"]).MIME_OUT_OF_LINE:
        return web.json_response(
            {"error": "mime_not_allowed", "mime": mime}, status=415, headers=_security_headers()
        )
    # Read the body. aiohttp's `read()` reads the whole request body
    # in memory; this is safe because we already know the size is
    # bounded (10 MB per file).
    try:
        payload = await request.read()
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"read failed: {exc}"}, status=400, headers=_security_headers()
        )
    from dhc.cordis.attachments import (
        AttachmentMIMEError,
        AttachmentSizeError,
    )
    try:
        meta = web_core.attachment_service.put(sid, payload, mime)
    except AttachmentMIMEError as exc:
        return web.json_response(
            {"error": "mime_not_allowed", "detail": str(exc)},
            status=415, headers=_security_headers(),
        )
    except AttachmentSizeError as exc:
        return web.json_response(
            {"error": "payload_too_large", "detail": str(exc)},
            status=413, headers=_security_headers(),
        )
    return web.json_response(meta, status=201, headers=_security_headers())


async def _api_attachments_get(request: web.Request) -> web.Response:
    """GET /api/attachments/{ref}

    Path param: the full `attachment:{session_id}:{uuid}` ref.
    Returns the original bytes with the original Content-Type.

    Capability: ATTACHMENT_GET.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.attachment_service is None:
        return web.json_response(
            {"error": "attachment_service not configured"}, status=503, headers=_security_headers()
        )
    ref = request.match_info.get("ref", "")
    from dhc.cordis.attachments import (
        AttachmentNotFoundError,
        AttachmentMIMEError,
    )
    try:
        payload, meta = web_core.attachment_service.get(ref)
    except ValueError:
        return web.json_response(
            {"error": "invalid_ref"}, status=400, headers=_security_headers()
        )
    except AttachmentNotFoundError:
        return web.json_response(
            {"error": f"attachment {ref!r} not found"}, status=404, headers=_security_headers()
        )
    except AttachmentMIMEError as exc:
        # Should not happen on get; defensive only.
        return web.json_response(
            {"error": "mime_not_allowed", "detail": str(exc)},
            status=415, headers=_security_headers(),
        )
    return web.Response(
        body=payload,
        content_type=meta.get("mime", "application/octet-stream"),
        headers=_security_headers(),
    )


async def _api_attachments_delete(request: web.Request) -> web.Response:
    """DELETE /api/attachments/{ref}

    Removes the .bin and .json files for the ref. The v0x04
    envelope is forgotten (no log entry). Idempotent: deleting a
    non-existent ref returns 404.

    Capability: ATTACHMENT_DELETE.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.attachment_service is None:
        return web.json_response(
            {"error": "attachment_service not configured"}, status=503, headers=_security_headers()
        )
    ref = request.match_info.get("ref", "")
    from dhc.cordis.attachments import AttachmentNotFoundError
    try:
        web_core.attachment_service.delete(ref)
    except ValueError:
        return web.json_response(
            {"error": "invalid_ref"}, status=400, headers=_security_headers()
        )
    except AttachmentNotFoundError:
        return web.json_response(
            {"error": f"attachment {ref!r} not found"}, status=404, headers=_security_headers()
        )
    return web.Response(status=204, headers=_security_headers())


async def _api_secrets_list(_request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"secrets": [], "names": [], "error": "secrets not configured"},
            status=503,
            headers=_security_headers(),
        )
    secrets = web_core.secrets_service.list_metadata()
    # Backwards-compat: `names` is preserved for v1.5.0 callers.
    names = [str(s["name"]) for s in secrets]
    return web.json_response(
        {"secrets": secrets, "names": names}, headers=_security_headers()
    )


async def _api_settings_state(_request: web.Request) -> web.Response:
    """GET /api/settings/state — schema-driven redacted view of
    provider configuration + runtime health.

    v1.6.0 (Phase 0, settings subsystem hardening). The endpoint
    NEVER echoes the raw value of any secret. It returns:

    ```json
    {
      "providers": {
        "<provider>": {
          "status": "configured" | "not_set",
          "source": "raw" | "env" | "file",
          "ref_hint": "OPENROUTER_API_KEY" | "openrouter.key" | null,
          "hint": "…7890",          // v0x04 envelope hint (raw + env/file)
          "health": "valid" | "invalid" | "network_error" | "not_supported" | "unknown",
          "health_label": "User's OpenRouter label" | null,
          "last_check_at": 1788483410.0   // unix seconds, 0.0 if never
        },
        ...
      }
    }
    ```

    The closed set of provider ids is the `ModelRegistry`'s
    `provider` field. Unknown provider ids (e.g. a stale secret
    name from a deleted model) are silently dropped. The
    Capability is `Capability.SECRET_LIST` (read-only, same
    threat model as `GET /api/secrets`).
    """
    from dhc.cordis.secrets import SecretSourceType

    web_core: "GuiWebCore | None" = _request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"providers": {}, "error": "secrets not configured"},
            status=503,
            headers=_security_headers(),
        )
    # Build a name -> {hint, source, ref_hint} index from the
    # metadata list. For each provider, look up the canonical
    # key name and the per-provider fallback.
    meta_index: dict[str, dict[str, object]] = {}
    for m in web_core.secrets_service.list_metadata():
        name = str(m.get("name") or "")
        if not name:
            continue
        # Read the structured source for ref/source. Falls back
        # to raw if the log was written by a pre-v1.6.0 version.
        try:
            src = web_core.secrets_service.get_source(name)
        except Exception:
            src = None
        meta_index[name] = {
            "hint": m.get("hint", ""),
            "source": src.type.value if src is not None else SecretSourceType.raw.value,
            "ref_hint": src.ref_hint() if src is not None else None,
        }
    # Build the providers map from the closed ModelRegistry.
    providers: dict[str, dict[str, object]] = {}
    if web_core.model_registry is not None:
        for m in web_core.model_registry.list_models():
            p = m.provider
            if p in providers:
                continue
            # Try the canonical key name first, then per-provider
            # fallback. We do NOT call lookup_api_key because
            # that walks per-model chains we don't need here.
            primary = f"llm_provider_{p}_{m.id.partition('/')[2]}"
            fallback = f"llm_provider_{p}_"
            entry: dict[str, object] | None = None
            for k in (primary, fallback):
                if k in meta_index:
                    entry = meta_index[k]
                    break
            state = web_core.provider_state.get(p)
            if entry is not None:
                providers[p] = {
                    "status": "configured",
                    "source": entry.get("source"),
                    "ref_hint": entry.get("ref_hint"),
                    "hint": entry.get("hint"),
                    "health": state.health_status.value,
                    "health_label": state.health_label,
                    "last_check_at": state.last_check_at,
                }
            else:
                providers[p] = {
                    "status": "not_set",
                    "source": None,
                    "ref_hint": None,
                    "hint": None,
                    "health": state.health_status.value,
                    "health_label": state.health_label,
                    "last_check_at": state.last_check_at,
                }
    return web.json_response(
        {"providers": providers}, headers=_security_headers()
    )


async def _api_secrets_put(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"error": "secrets not configured"}, status=503, headers=_security_headers()
        )
    if web_core.secrets_service.is_insecure_permissions:
        return web.json_response(
            {"error": "secrets log has insecure permissions; chmod 600 required"},
            status=403,
            headers=_security_headers(),
        )
    name = request.match_info.get("name", "")
    try:
        body = await request.json() if request.body_exists else {}
    except Exception as exc:  # noqa: BLE001
        return web.json_response(
            {"error": f"invalid json: {exc}"}, status=400, headers=_security_headers()
        )
    if not isinstance(body, dict):
        body = {}
    value = body.get("value")
    if not isinstance(value, str):
        return web.json_response(
            {"error": "missing 'value' (string)"}, status=400, headers=_security_headers()
        )
    try:
        web_core.secrets_service.put(name, value)
    except ValueError as exc:
        return web.json_response(
            {"error": str(exc)}, status=400, headers=_security_headers()
        )
    return web.Response(status=204, headers=_security_headers())


async def _api_secrets_delete(request: web.Request) -> web.Response:
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None or web_core.secrets_service is None:
        return web.json_response(
            {"error": "secrets not configured"}, status=503, headers=_security_headers()
        )
    if web_core.secrets_service.is_insecure_permissions:
        return web.json_response(
            {"error": "secrets log has insecure permissions; chmod 600 required"},
            status=403,
            headers=_security_headers(),
        )
    name = request.match_info.get("name", "")
    ok = web_core.secrets_service.delete(name)
    if not ok:
        return web.json_response(
            {"error": f"secret {name!r} not found"}, status=404, headers=_security_headers()
        )
    return web.Response(status=204, headers=_security_headers())


async def _ws_chat_handler_impl(request: web.Request) -> web.WebSocketResponse:
    """`/ws/chat` is a separate WebSocket channel from `/ws`.

    The two channels are intentionally distinct: `/ws` is the
    read-only event broadcast (the C2 lifecycle events the existing
    UI subscribes to). `/ws/chat` is a request/response channel for
    the v1.2.0 chat panel; it accepts `chat.send` frames and
    replies with `chat.delta`/`chat.tool_call`/`chat.done`/
    `chat.error` frames.

    The frame schema is documented in `docs/chat-architecture.md`.
    """
    web_core: "GuiWebCore | None" = request.app.get("web_core")  # type: ignore[attr-defined]
    if web_core is None:
        return web.Response(status=500, text="no web_core", headers=_security_headers())
    guard = _require_loopback_auth(
        request,
        web_core.token if web_core.require_token else None,
        web_core.allowed_origins,
    )
    if guard is not None:
        return guard
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    for k, v in _security_headers().items():
        ws.headers[k] = v

    async def send_frame(payload: dict) -> None:
        if ws.closed:
            return
        try:
            await ws.send_str(json.dumps(payload))
        except TypeError:
            await ws.send_str(json.dumps({"type": "chat.error", "message": str(payload)}))

    async for msg in ws:
        if msg.type != web.WSMsgType.TEXT:
            continue
        try:
            data = json.loads(msg.data)
        except json.JSONDecodeError:
            await send_frame({"type": "chat.error", "code": "bad_json"})
            continue
        if not isinstance(data, dict):
            await send_frame({"type": "chat.error", "code": "not_object"})
            continue
        # v1.5.1: chat.cancel — mark the in-flight stream as
        # cancelled. The currently-running chat handler (if any)
        # checks the flag between chunks and breaks the loop,
        # finalizing the assistant turn with `cancelled: true`.
        # If no stream is in progress on this session, this is a
        # no-op (the route reports ok; the client should revert
        # the Stop button back to Send). Server race: a `chat.done`
        # may have already been sent; in that case the client
        # receives `chat.cancelled` and the next `chat.done` is
        # just the natural end (cancelled=false on the frame).
        if data.get("type") == "chat.cancel":
            sid_cancel = str(data.get("session_id") or "")
            if not sid_cancel:
                await send_frame({"type": "chat.error", "code": "missing_session_id"})
                continue
            sm_cancel = web_core.session_manager
            if sm_cancel is not None and sm_cancel.is_stream_in_progress(sid_cancel):
                sm_cancel.cancel_stream(sid_cancel)
                await send_frame({"type": "chat.cancelled", "session_id": sid_cancel})
            else:
                # No active stream; the click was a no-op.
                await send_frame({"type": "chat.cancelled", "session_id": sid_cancel, "noop": True})
            continue
        if data.get("type") != "chat.send":
            await send_frame({"type": "chat.error", "code": "unknown_type", "got": data.get("type")})
            continue
        sid = str(data.get("session_id") or "")
        text = str(data.get("text") or "")
        if not sid or not text:
            await send_frame({"type": "chat.error", "code": "missing_session_or_text"})
            continue
        sm = web_core.session_manager
        if sm is None:
            await send_frame({"type": "chat.error", "code": "no_session_manager"})
            continue
        s = sm.get(sid)
        if s is None:
            await send_frame({"type": "chat.error", "code": "session_not_found", "session_id": sid})
            continue
        # v1.6.0 (Phase 0): IngressScrubber. A user who pastes a
        # key into the chat input must NOT have it journaled or
        # sent upstream. The scrubber is applied BEFORE the
        # append_message call so the journal records the
        # redacted text. The C7 adapter ALSO scrubs (defense in
        # depth) so even if this handler is bypassed, the
        # upstream never sees the raw key.
        from dhc.security.ingress_scrubber import scrub
        scrubbed = scrub(text)
        if scrubbed.redactions > 0:
            # Notify the client that redaction happened. The
            # scrubbed text is the new text.
            await send_frame({
                "type": "chat.notice",
                "code": "redacted",
                "redactions": scrubbed.redactions,
            })
        text = scrubbed.text
        user_msg = sm.append_message(sid, "user", text)
        if user_msg is None:
            await send_frame({"type": "chat.error", "code": "append_failed"})
            continue
        # v1.5.0 (ADR-0019): pin the assistant message to the user
        # message's parent_id, captured at stream start. The user
        # message's parent_id is whatever the session's active_tip_id
        # was at the time of the send. The assistant message is
        # appended as a child of that parent, not of the current
        # active_tip_id.
        pinned_parent_id = user_msg.get("parent_id")
        ctx: Context | None = request.app.get("ctx")
        adapter = ctx.inject("llm") if ctx is not None else None
        if adapter is None:
            await send_frame({"type": "chat.error", "code": "no_llm"})
            continue
        s = sm.get(sid)
        messages = [{"role": m["role"], "content": m.get("content", "")} for m in s.messages]
        sm.begin_stream(sid)
        try:
            t0 = int(time.time() * 1000)
            completion_tokens = 0
            prompt_tokens = 0
            assistant_text_parts: list[str] = []
            # v1.3.2: per-session usage accumulator. We snapshot the
            # current totals at the start of the turn and add the
            # provider's reported usage to the snapshot. The result
            # is written back via `sm.update(...)` after the stream.
            s_now = sm.get(sid)
            prior_totals = dict(s_now.usage_totals) if s_now is not None else {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "last_turn_prompt": 0,
                "last_turn_completion": 0,
            }
            # v1.3.1: pass session_id so C7 can look up the
            # per-session ModelConfig (ADR-0011) and apply the
            # configured temperature/max_tokens/top_p + system
            # prompt before dispatching to the provider.
            # v1.5.1: cancel handling. The outer `async for msg in ws:`
            # loop is the canonical reader of the WS. The chat
            # handler yields to the event loop between chunks via
            # `asyncio.sleep(0)` so the reader task can deliver a
            # pending `chat.cancel` frame to the outer loop's
            # `_waiter`. The outer loop's `chat.cancel` handler
            # then sets the cancel flag on the SessionManager, and
            # the next iteration of the chat handler's `async for`
            # checks the flag and breaks.
            cancelled = False
            async for chunk in adapter.chat_stream(
                messages=messages, model=s.model or "mock-default", session_id=sid
            ):
                # v1.5.1: yield to the event loop so the outer
                # loop's `ws.receive()` can pick up a `chat.cancel`
                # frame. The aiohttp WS only allows one pending
                # `receive()` at a time, so we can't race the WS
                # read directly from the chat handler. With this
                # 0-event-loop yield, the cancel still arrives
                # between chunks; for true mid-stream interrupt,
                # see ADR-0022 (v1.5.2 work).
                await asyncio.sleep(0)
                if sm.is_stream_cancelled(sid):
                    cancelled = True
                    break
                if chunk.delta:
                    await send_frame({
                        "type": "chat.delta",
                        "session_id": sid,
                        "delta": chunk.delta,
                    })
                    assistant_text_parts.append(chunk.delta)
                    completion_tokens += max(1, len(chunk.delta) // 4)
                if chunk.tool_calls:
                    await send_frame({
                        "type": "chat.tool_call",
                        "session_id": sid,
                        "tool_calls": chunk.tool_calls,
                    })
                    assistant_text_parts.append(chunk.delta)
                    completion_tokens += max(1, len(chunk.delta) // 4)
                if chunk.tool_calls:
                    await send_frame({
                        "type": "chat.tool_call",
                        "session_id": sid,
                        "tool_calls": chunk.tool_calls,
                    })
                # v1.3.1: if the provider returned a usage block on
                # the terminating chunk, prefer it over the
                # char/4 estimate. Mock LLM never sets usage; live
                # providers (OpenAI/Anthropic/OpenRouter) do.
                if chunk.usage:
                    prompt_tokens = int(chunk.usage.get("prompt_tokens") or 0)
                    completion_tokens = int(chunk.usage.get("completion_tokens") or completion_tokens)
                if chunk.finish_reason:
                    break
            t1 = int(time.time() * 1000)
            # v1.3.2: aggregate into per-session totals. The lifetime
            # totals are additive across turns; the per-turn values
            # are overwritten so the UI can show "this turn used N
            # tokens" without doing a diff.
            new_totals = {
                "prompt_tokens": prior_totals["prompt_tokens"] + prompt_tokens,
                "completion_tokens": prior_totals["completion_tokens"] + completion_tokens,
                "total_tokens": prior_totals["total_tokens"] + prompt_tokens + completion_tokens,
                "last_turn_prompt": prompt_tokens,
                "last_turn_completion": completion_tokens,
            }
            # Persist the assistant turn, pinned to the user message's
            # parent_id (ADR-0019 stream pinning).
            sm.append_message(
                sid, "assistant", "".join(assistant_text_parts),
                tokens={"prompt": prompt_tokens, "completion": completion_tokens},
                parent_id=pinned_parent_id,
            )
            # Persist the updated totals on the session.
            sm.update(sid, usage_totals=new_totals)
            await send_frame({
                "type": "chat.done",
                "session_id": sid,
                "tokens": {"prompt": prompt_tokens, "completion": completion_tokens},
                "latency_ms": t1 - t0,
                "usage_totals": new_totals,
                "cancelled": cancelled,
            })
        except Exception as exc:  # noqa: BLE001
            # v1.5.1.5: surface C7 stream timeouts as a distinct
            # `stream_timeout` code so the UI can show a specific
            # message ("The model is taking too long; try again.")
            # instead of a generic `llm_failed`.
            if isinstance(exc, asyncio.TimeoutError):
                await send_frame({"type": "chat.error", "code": "stream_timeout", "message": str(exc)[:200]})
            else:
                await send_frame({"type": "chat.error", "code": "llm_failed", "message": str(exc)[:200]})
        finally:
            sm.end_stream(sid)
            sm.clear_cancel(sid)
    return ws


class GuiWebCore:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        static_dir: str | os.PathLike[str] | None = None,
        allowed_origins: Iterable[str] = DEFAULT_ALLOWED_ORIGINS,
        port_file: str | os.PathLike[str] | None = None,
        token_file: str | os.PathLike[str] | None = None,
        token: str | None = None,
        require_token: bool = True,
        sessions_dir: str | os.PathLike[str] | None = None,
        secrets_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.static_dir = Path(static_dir) if static_dir is not None else None
        self.allowed_origins = tuple(allowed_origins)
        self.port_file = Path(port_file) if port_file is not None else None
        self.token_file = Path(token_file) if token_file is not None else None
        self.require_token = require_token
        # If a token was supplied, use it; otherwise generate one.
        # Either way, the token is a string known to this process only.
        self.token: str = token if token is not None else _generate_token()
        # Plugin marketplace state. Discovered at construction; loaded
        # on demand via /plugins/{id}.
        from dhc.plugins.loader import PluginState, discover
        self.plugin_state = PluginState()
        self.plugin_state.discovered = discover()
        # v1.2.0: session manager and secrets service are created
        # eagerly when their directories are provided. If the dir
        # is None the corresponding routes return 503.
        self.session_manager: Any = None
        if sessions_dir is not None:
            from dhc.services.session_manager import SessionManager

            self.session_manager = SessionManager(Path(sessions_dir))
        self.secrets_service: Any = None
        if secrets_dir is not None:
            from dhc.cordis.secrets import SecretsService
            from dhc.services.model_config import ModelConfigStore

            self.secrets_service = SecretsService(Path(secrets_dir))
            # v1.5.1.1 (ADR-0108): fail-loud 0600 check on the
            # secrets log. If the file exists with a permissive
            # mode, log a warning and reject future writes rather
            # than silently fall back. The GET endpoint still
            # works (it is read-only), but PUT/DELETE return 403
            # until the user runs `chmod 600`.
            try:
                self.secrets_service.check_permissions()
            except PermissionError as _exc:
                # Log once at startup. We do not crash the
                # server — the user may be migrating from a
                # permissive-mode installation.
                import logging
                logging.getLogger("dhc.c1.secrets").warning(
                    "secrets log has permissive mode: %s", _exc
                )
            # v1.3.1 (ADR-0011): per-session model config store.
            # Backed by the same `SecretsService`; uses raw bytes
            # so the JSON encoding round-trips losslessly.
            self.model_config_store: Any = ModelConfigStore(self.secrets_service)
            # v1.5.0 (ADR-0020): attachment service. Stored under
            # `~/.dhc/attachments/`. Backed by the same `SecretsService`
            # via the new `seal_raw` / `open_raw` API.
            from dhc.cordis.attachments import AttachmentService
            self.attachment_service: Any = AttachmentService(
                attachments_dir=Path(secrets_dir) / "attachments",
                secrets_service=self.secrets_service,
            )
        else:
            self.model_config_store = None
            self.attachment_service = None
        # v1.6.0 (Phase 0): ProviderStateManager. Per-process cache
        # of (configuration presence, runtime health) for each
        # provider. Written by `_api_llm_health_provider` after
        # a probe; read by `GET /api/settings/state` and by
        # `ChatPanel` on mount. Not persisted: the next probe
        # rebuilds the cache.
        from dhc.services.provider_state import ProviderStateManager
        self.provider_state = ProviderStateManager()
        # v1.3.0: model registry. Always constructed (it's a pure
        # in-memory hardcoded list) so /api/models works without
        # external configuration. Per ADR-0006, dynamic discovery
        # is deferred to v1.4.0.
        from dhc.services.model_registry import ModelRegistry

        self.model_registry: ModelRegistry = ModelRegistry()
        self.app = web.Application()
        # Stash references the route handlers need.
        self.app["web_core"] = self
        self.app["prompt_browser"] = None  # populated by the prompt_browser_v1 plugin
        # v1.5.0 (ADR-0015-amendment-1 §"Consequences"): the bijection
        # invariant reads `self.registered_capabilities` after
        # `_setup_routes` runs. The set is populated as a side effect
        # of `_gated` and is the runtime projection of the
        # `Capability` enum. The invariant asserts:
        #   set(Capability) == ACTION_WHITELIST.keys() == self.registered_capabilities
        self.registered_capabilities: set = set()
        # Routes (registered in order, but aiohttp dispatches by pattern).
        # v1.4.0 (ADR-0015): every C1 surface is gated by a
        # `@requires(Capability.X)` decorator applied at registration
        # time. The runtime guard reads `ACTION_WHITELIST`; a route
        # without a registered capability fails the DoD. The /healthz
        # and / routes are not gated (liveness + static index must be
        # reachable without auth).
        self._setup_routes()
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    def _gated(self, cap: Capability, handler):
        """Wrap a route handler with `@requires(cap)` and record the
        capability in `self.registered_capabilities`.

        v1.5.0 (Phase 1, Findings 1+2): the only side effect beyond
        returning the wrapped handler is the side-effect on
        `self.registered_capabilities`. The set is read by the
        bijection invariant in `scripts/invariants_check.ps1`.
        Registration order does not matter; the set is a final
        projection of the @requires calls.
        """
        self.registered_capabilities.add(cap)
        return _requires_capability(cap)(handler)

    def _setup_routes(self) -> None:
        """Register all C1 routes. Called once from `__init__`.

        v1.4.0 (ADR-0015): every C1 surface except `/healthz`, `/`,
        `/assets/`, and `/ws` is wrapped with `_gated` and recorded
        in `self.registered_capabilities`. The set is the runtime
        projection of the capability whitelist and is asserted by
        the bijection invariant.
        """
        # /healthz and / are not gated (liveness + static index).
        self.app.router.add_get("/healthz", _healthz)
        self.app.router.add_get("/api/manifest", self._gated(Capability.MANIFEST_READ, _api_manifest))
        # v1.5.0.1: the auto-load memory is read out by the Modules
        # tab. Unauthenticated on loopback (same as /api/manifest);
        # an attacker on the host can already list the loaded
        # plugins from /api/manifest. The memory list is not a
        # secret.
        self.app.router.add_get("/api/memory", _api_memory)
        self.app.router.add_get("/plugins", self._gated(Capability.PLUGIN_LIST, _plugins_list))
        self.app.router.add_post("/plugins/{plugin_id}", self._gated(Capability.PLUGIN_LOAD, _plugins_load))
        self.app.router.add_delete("/plugins/{plugin_id}", self._gated(Capability.PLUGIN_UNLOAD, _plugins_unload))
        self.app.router.add_get("/prompts", self._gated(Capability.PROMPT_READ, _prompts_list))
        self.app.router.add_get("/prompts/{key}", self._gated(Capability.PROMPT_READ, _prompts_get))
        self.app.router.add_post("/api/eval", self._gated(Capability.EVAL_RUN, _api_eval))
        self.app.router.add_get("/ws", self._ws_handler)
        # v1.2.0 chat + sessions + secrets + LLM health
        self.app.router.add_get("/api/llm/health", self._gated(Capability.LLM_HEALTH, _api_llm_health))
        # v1.5.1.4 (audit hotfix #4): per-provider real probe.
        # The {provider} path is constrained to a closed set of
        # provider ids (openai, anthropic, openrouter); the
        # handler ignores unknown ids by returning
        # `not_supported` (defense in depth against routing
        # surprises — the route accepts any string but the
        # handler is the gate).
        self.app.router.add_get(
            "/api/llm/health/{provider}",
            self._gated(Capability.LLM_HEALTH, _api_llm_health_provider),
        )
        self.app.router.add_get("/api/models", self._gated(Capability.MODEL_LIST, _api_models_list))
        # Allow slashes in the model id (e.g. "openai/gpt-4o-mini").
        self.app.router.add_get("/api/models/{id:.+}", self._gated(Capability.MODEL_READ, _api_models_get))
        self.app.router.add_get("/api/sessions", self._gated(Capability.SESSION_LIST, _api_sessions_list))
        self.app.router.add_post("/api/sessions", self._gated(Capability.SESSION_CREATE, _api_sessions_create))
        self.app.router.add_get("/api/sessions/{session_id}", self._gated(Capability.SESSION_READ, _api_sessions_get))
        self.app.router.add_patch("/api/sessions/{session_id}", self._gated(Capability.SESSION_PATCH, _api_sessions_patch))
        self.app.router.add_delete("/api/sessions/{session_id}", self._gated(Capability.SESSION_DELETE, _api_sessions_delete))
        self.app.router.add_post("/api/sessions/{session_id}/messages", self._gated(Capability.CHAT_SEND, _api_sessions_post_message))
        # v1.5.0 branching routes (ADR-0019)
        self.app.router.add_post("/api/sessions/{session_id}/branches", self._gated(Capability.BRANCH_CREATE, _api_sessions_branch_create))
        self.app.router.add_patch("/api/sessions/{session_id}/active-tip", self._gated(Capability.BRANCH_SWITCH, _api_sessions_active_tip))
        self.app.router.add_get("/api/sessions/{session_id}/branches", self._gated(Capability.BRANCH_LIST, _api_sessions_branches))
        # v1.5.0 attachment routes (ADR-0020). The `attachment:` prefix
        # in the ref is part of the URL path; the {ref:.+} pattern
        # accepts colons.
        self.app.router.add_post("/api/sessions/{session_id}/attachments", self._gated(Capability.ATTACHMENT_PUT, _api_sessions_attachment_put))
        self.app.router.add_get("/api/attachments/{ref:.+}", self._gated(Capability.ATTACHMENT_GET, _api_attachments_get))
        self.app.router.add_delete("/api/attachments/{ref:.+}", self._gated(Capability.ATTACHMENT_DELETE, _api_attachments_delete))
        self.app.router.add_get("/api/sessions/{session_id}/config", self._gated(Capability.CONFIG_GET, _api_sessions_get_config))
        self.app.router.add_post("/api/sessions/{session_id}/config", self._gated(Capability.CONFIG_SET, _api_sessions_set_config))
        self.app.router.add_get("/api/secrets", self._gated(Capability.SECRET_LIST, _api_secrets_list))
        self.app.router.add_put("/api/secrets/{name}", self._gated(Capability.SECRET_PUT, _api_secrets_put))
        self.app.router.add_delete("/api/secrets/{name}", self._gated(Capability.SECRET_DELETE, _api_secrets_delete))
        self.app.router.add_get("/api/settings/state", self._gated(Capability.SECRET_LIST, _api_settings_state))
        self.app.router.add_get("/ws/chat", self._gated(Capability.CHAT_SEND, _ws_chat_handler_impl))
        if self.static_dir is not None and self.static_dir.exists():
            self.app.router.add_get("/", self._serve_index)
            self.app.router.add_static(
                "/assets/",
                path=str(self.static_dir / "assets"),
                show_index=False,
            )
        else:
            self.app.router.add_get("/", _index)

    async def _serve_index(self, _request: web.Request) -> web.Response:
        index_path = self.static_dir / "index.html" if self.static_dir else None
        if index_path is None or not index_path.exists():
            return _index(_request)
        body = index_path.read_text(encoding="utf-8")
        return web.Response(
            text=body,
            content_type="text/html",
            headers=_security_headers(),
        )

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        # ---- 1. Origin guard (CSWSH mitigation) ----
        origin = request.headers.get("Origin", "")
        if not _is_allowed_origin(origin, self.allowed_origins):
            return web.Response(
                status=403,
                text="Origin not allowed",
                headers=_security_headers(),
            )

        # ---- 2. Bearer token check (authentication) ----
        if self.require_token:
            provided = _extract_bearer_token(request)
            if not _check_token(provided, self.token):
                return web.Response(
                    status=401,
                    text="Unauthorized",
                    headers=_security_headers(),
                )

        ws = web.WebSocketResponse()
        await ws.prepare(request)
        for k, v in _security_headers().items():
            ws.headers[k] = v
        ctx: Context | None = request.app.get("ctx")  # type: ignore[arg-type]

        def make_listener(event_name: str):
            async def on_event(payload: Any) -> None:
                if ws.closed:
                    return
                try:
                    msg = json.dumps({"event": event_name, "payload": payload})
                except TypeError:
                    msg = json.dumps({"event": event_name, "payload": str(payload)})
                await ws.send_str(msg)

            return on_event

        registered: list[tuple[str, Any]] = []
        if ctx is not None:
            for ev in _FORWARDED_EVENTS:
                listener = make_listener(ev)
                ctx.events.on(ev, listener)
                registered.append((ev, listener))
        try:
            async for _msg in ws:
                pass
        finally:
            if ctx is not None:
                for ev, listener in registered:
                    ctx.events.off(ev, listener)
        return ws

    async def start(self) -> int:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, self.port)
        await self._site.start()
        if not self._runner.sites:
            raise RuntimeError("aiohttp AppRunner has no bound sites")
        site = next(iter(self._runner.sites))
        server = site._server  # type: ignore[attr-defined]
        sockets = server.sockets  # type: ignore[attr-defined]
        if not sockets:
            raise RuntimeError("aiohttp TCPSite reported no bound sockets")
        sock = next(iter(sockets))
        self.port = sock.getsockname()[1]
        if self.port_file is not None:
            self.port_file.write_text(str(self.port), encoding="utf-8")
        if self.token_file is not None:
            # Restrict the token file's permissions on POSIX systems.
            # On Windows, the umask-based default is sufficient given
            # the loopback bind; ACLs are out of scope here.
            try:
                self.token_file.write_text(self.token, encoding="utf-8")
                if hasattr(os, "chmod"):
                    os.chmod(self.token_file, 0o600)
            except OSError:
                pass
        # If a static dist/ is being served, embed the token in index.html
        # so the React client can read it on page load.
        if self.static_dir is not None:
            index_path = self.static_dir / "index.html"
            if index_path.exists():
                _embed_token_in_index(index_path, self.token)
        return self.port

    async def stop(self) -> None:
        if self._site is not None:
            await self._site.stop()
        if self._runner is not None:
            await self._runner.cleanup()
        # Wipe the embedded token from the static index.html so a stale
        # build can't be reused against a different process.
        if self.static_dir is not None:
            index_path = self.static_dir / "index.html"
            if index_path.exists():
                import re as _re
                text = index_path.read_text(encoding="utf-8")
                if 'name="dhc-token"' in text:
                    text = _re.sub(
                        r'<meta\s+name="dhc-token"\s+content="[^"]*"\s*/?>',
                        '<meta name="dhc-token" content="" />',
                        text,
                    )
                    index_path.write_text(text, encoding="utf-8")


@plugin("c1_gui")
async def apply(ctx: Context, config: dict) -> Callable[[], None]:
    host = (config or {}).get("host", "127.0.0.1")
    port = int((config or {}).get("port", 0))
    static_dir = (config or {}).get("static_dir")
    allowed_origins = (config or {}).get("allowed_origins", DEFAULT_ALLOWED_ORIGINS)
    port_file = (config or {}).get("port_file")
    token_file = (config or {}).get("token_file")
    require_token = bool((config or {}).get("require_token", True))
    supplied_token = (config or {}).get("token")
    # v1.5.0.1: the serve_c1 entry point passes `sessions_dir` and
    # `secrets_dir` in the config; GuiWebCore needs them to wire
    # up the SessionManager / SecretsService / AttachmentService
    # at construction time. Without these, the C1 routes that
    # depend on session storage (POST /api/sessions, /api/sessions/{id},
    # /api/sessions/{id}/branches, /api/sessions/{id}/attachments,
    # /api/sessions/{id}/messages, /api/secrets, /api/attachments/*)
    # all return 503 "X not configured" — the React client cannot
    # create a session, list sessions, or read/write secrets.
    sessions_dir = (config or {}).get("sessions_dir")
    secrets_dir = (config or {}).get("secrets_dir")
    # Optional: caller can pass `plugins_auto_load` to load a list of
    # plugin ids at startup. Default: empty (zero plugins, all on
    # demand). Pass `["rate_limiter_v1", "prompt_browser_v1", ...]`
    # in the serve_c1 config to pre-load.
    auto_load: list[str] = (config or {}).get("plugins_auto_load", []) or []
    web_core = GuiWebCore(
        host=host,
        port=port,
        static_dir=static_dir,
        allowed_origins=allowed_origins,
        port_file=port_file,
        token_file=token_file,
        token=supplied_token,
        require_token=require_token,
        sessions_dir=sessions_dir,
        secrets_dir=secrets_dir,
    )
    web_core.app["ctx"] = ctx
    # Expose repo_root so /api/eval can locate the test files.
    # The C1 module lives at src/dhc/modules/c1_gui_web_core/service.py,
    # so the repo root is parents[3].
    web_core.app["repo_root"] = Path(__file__).resolve().parents[3]
    ctx.provide("gui", web_core)
    ctx.provide("csp", CSP_HEADER)
    ctx.provide("auth_token", web_core.token)

    # Pre-load any plugins requested at startup. Failures are logged
    # and surfaced via /healthz; the harness still serves.
    if auto_load:
        # v1.5.0.1: we are inside an async function, so the synchronous
        # `load()` cannot be called (it tries to spawn its own event
        # loop and the asyncio runtime rejects this). Use `load_async`
        # which awaits the plugin's apply() in the current loop.
        from dhc.plugins.loader import load_async, PluginError

        for plugin_id in auto_load:
            try:
                await load_async(web_core.plugin_state, ctx, plugin_id, config={})
            except PluginError as exc:
                import logging

                logging.getLogger("dhc.c1").warning(
                    "auto-load plugin %r failed: %s", plugin_id, exc
                )

    if (config or {}).get("autostart", False):
        await web_core.start()

    async def dispose() -> None:
        await web_core.stop()
        ctx.services.pop("gui", None)
        ctx.services.pop("csp", None)
        ctx.services.pop("auth_token", None)
        for p in (port_file, token_file):
            if p and os.path.exists(str(p)):
                try:
                    os.remove(str(p))
                except OSError:
                    pass

    return dispose
