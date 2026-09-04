"""dhc.cordis.number_gate: lossless byte-to-code canonicalization and the
62-symbol legal-charset filter for the audit-render boundary (ADR-0016).

Two functions are exported:

- `to_codes(data: bytes) -> list[int]`: lossless. Every byte becomes one
  integer code (0..255). No information is lost.
- `from_codes(codes, *, strict: bool = True) -> str`: project codes to
  text. `strict=True` (audit render): non-legal codes become `#0xNN`
  escape tokens. `strict=False` (provider boundary): reconstruct the
  exact original bytes.

The legal alphabet is the 62 codes `[A-Z a-z 0-9]`. The set is frozen
at the v1.4.0 release. Adding or removing a code requires an ADR.

A small set of helpers is exported for the test generator and the
audit-render path:
- `LEGAL_TEXT_CODES`: the 62-code `frozenset[int]`.
- `NUMERIC_ESCAPE`: the prefix character for `#0xNN` escapes (literal
  `'#'`, not a comment marker).
- `is_legal_code(code: int) -> bool`: `code in LEGAL_TEXT_CODES`.
- `render_audit_text(data: bytes | str) -> str`: a convenience wrapper
  that runs `to_codes` then `from_codes(strict=True)`. If `data` is a
  `str`, it is encoded to UTF-8 first (lossless for ASCII; non-ASCII
  bytes are projected as escapes).
- `render_provider_bytes(data: bytes | str) -> bytes`: the provider
  boundary. `from_codes(strict=False)` returns a `bytes` object
  reconstructed from the codes. If `data` is a `str`, it is encoded to
  UTF-8 first.
- `enumerate_byte_code_tests() -> list[pytest.param]`: 256 parametrized
  cases for the byte-code sweep test.

The filter applies at the audit-render boundary, NEVER at the
user-input boundary. A user typing `Hello, world!` gets exactly that
in the in-memory `SessionEvent.payload`. The audit log gets the
62-code projection: `Hello#0x2C world#0x21`. The provider boundary
reconstructs the exact bytes at the moment the harness hands a
secret to a provider.
"""
from __future__ import annotations

from typing import Iterable, Union

import pytest


# 62 codes: [A-Z a-z 0-9]. The set is frozen at the v1.4.0 release;
# adding or removing a code requires an ADR.
LEGAL_TEXT_CODES: frozenset[int] = frozenset(
    list(range(0x30, 0x3A))   # 0-9
    + list(range(0x41, 0x5B))  # A-Z
    + list(range(0x61, 0x7B))  # a-z
)
assert len(LEGAL_TEXT_CODES) == 62  # invariant: 10 + 26 + 26

NUMERIC_ESCAPE: str = "#"  # prefix character for `#0xNN` escapes


def is_legal_code(code: int) -> bool:
    """True iff `code` is in the 62-code legal alphabet."""
    return code in LEGAL_TEXT_CODES


def to_codes(data: bytes) -> list[int]:
    """Lossless: every byte becomes one integer code (0..255)."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("to_codes requires bytes or bytearray")
    return list(data)


def _escape_token(code: int) -> str:
    """Render a non-legal code as `#0xNN` (uppercase hex)."""
    return f"{NUMERIC_ESCAPE}0x{code:02X}"


def from_codes(
    codes: Iterable[int],
    *,
    strict: bool = True,
) -> Union[str, bytes]:
    """Project a code sequence to text or to bytes.

    `strict=True` (audit render): legal codes pass as their ASCII
    character; non-legal codes become `#0xNN` escape tokens. The return
    type is `str`.

    `strict=False` (provider boundary): reconstruct the exact original
    bytes. The return type is `bytes`. The caller is expected to use
    this at the provider HTTP request body, not at the audit log.
    """
    out_chars: list[str] = []
    out_bytes: bytearray | None = bytearray() if not strict else None
    for c in codes:
        code = int(c)
        if not 0 <= code <= 255:
            raise ValueError(f"code out of range: {code}")
        if strict:
            if is_legal_code(code):
                out_chars.append(chr(code))
            else:
                out_chars.append(_escape_token(code))
        else:
            assert out_bytes is not None
            out_bytes.append(code)
    if strict:
        return "".join(out_chars)
    assert out_bytes is not None
    return bytes(out_bytes)


def render_audit_text(data: Union[bytes, str]) -> str:
    """Convenience: `to_codes` + `from_codes(strict=True)`.

    If `data` is a `str`, it is encoded to UTF-8 first (lossless for
    ASCII; non-ASCII bytes are projected as escapes). This is the
    audit-render call. The in-memory `SessionEvent.payload` is
    unchanged; this function is the render boundary.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return from_codes(to_codes(data), strict=True)


def render_provider_bytes(data: Union[bytes, str]) -> bytes:
    """Convenience: `to_codes` + `from_codes(strict=False)`.

    This is the provider-boundary call. The exact original bytes are
    returned. The audit log never sees this function; the audit log
    only ever calls `render_audit_text`.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    out = from_codes(to_codes(data), strict=False)
    assert isinstance(out, bytes)
    return out


def enumerate_byte_code_tests() -> list[pytest.param]:
    """Generator: one `pytest.param` per byte code (0..255).

    The byte-code sweep test in `tests/cordis/test_number_gate.py`
    parametrizes over the result and asserts the strict projection
    matches the expected output.
    """
    return [
        pytest.param(
            code,
            id=f"code_{code:03d}_0x{code:02X}",
        )
        for code in range(256)
    ]


__all__ = [
    "LEGAL_TEXT_CODES",
    "NUMERIC_ESCAPE",
    "is_legal_code",
    "to_codes",
    "from_codes",
    "render_audit_text",
    "render_provider_bytes",
    "enumerate_byte_code_tests",
]
