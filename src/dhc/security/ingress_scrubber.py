"""dhc.security.ingress_scrubber: regex pipeline that redacts
known API key prefixes from inbound user text.

v1.6.0 (Phase 0, security hardening). The 2026-09-09 live
verification
(`docs/verification/2026-09-09-openrouter-probe.md`) found
that the WebSocket chat handler passed the user's `text` field
directly to the LLM adapter and persisted it in the session
journal, with no redaction. A user who pastes a key into the
chat input would have the key sent upstream to the LLM
provider and persisted on disk.

`scrub()` closes that gap. It is a compiled-regex pipeline
that scans inbound `chat` and `system` prompts for known
provider prefixes (`sk-or-v1-`, `sk-ant-`, `sk-proj-`) and
replaces matches with `[REDACTED_API_KEY]`. The scrubber is
applied at two points (defense in depth):

1. The WS handler in `c1_gui_web_core.service`, before
   `sm.append_message(sid, "user", text)` — the redacted
   text is what gets journaled.
2. The C7 adapter in `c7_llm_stream_adapter.service`, before
   the LLM call — even if the WS handler is bypassed, the
   upstream never sees the raw key.

Bounded entropy: the set of patterns is closed. New providers
require a kernel ADR; the canonical list lives in
`PROVIDER_KEY_PATTERNS` below.

The scrubber does NOT touch outbound responses. The
no-leak-contract for the *response* side is enforced by
`_api_llm_health_provider` (per ADR-0108) and the v0x04
envelope; the scrubber's job is the *inbound* side.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Compiled regexes for known provider key prefixes. Each pattern
# is anchored on the prefix and requires at least 20 characters
# after the dash to avoid false positives on short tokens like
# "sk-" in user text. (The generic `sk-` pattern is intentionally
# NOT included; it would match common words and URLs.)
#
# v1.6.0: this set is closed. New provider patterns require a
# kernel ADR (so the scrubber cannot grow without review).
PROVIDER_KEY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-or-v1-[A-Za-z0-9]{20,}"),       # OpenRouter
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),      # Anthropic
    re.compile(r"sk-proj-[A-Za-z0-9_\-]{20,}"),     # OpenAI project keys
)

REDACTED = "[REDACTED_API_KEY]"


@dataclass(frozen=True)
class ScrubResult:
    """Outcome of a `scrub` call. `original_len` and
    `redacted_len` are exposed so callers (and tests) can
    assert that redaction actually happened without needing
    to compare the input and output strings.
    """

    text: str
    redactions: int
    original_len: int
    redacted_len: int


def scrub(text: str) -> ScrubResult:
    """Apply the v1.6.0 IngressScrubber pipeline to `text`.

    Returns a `ScrubResult` with the redacted text and counts.
    The function is pure: no I/O, no logging, no side effects.
    """
    if not text:
        return ScrubResult(text=text, redactions=0, original_len=0, redacted_len=0)
    out = text
    redactions = 0
    for pat in PROVIDER_KEY_PATTERNS:
        out, n = pat.subn(REDACTED, out)
        redactions += n
    return ScrubResult(
        text=out,
        redactions=redactions,
        original_len=len(text),
        redacted_len=len(out),
    )


__all__ = [
    "PROVIDER_KEY_PATTERNS",
    "REDACTED",
    "ScrubResult",
    "scrub",
]
