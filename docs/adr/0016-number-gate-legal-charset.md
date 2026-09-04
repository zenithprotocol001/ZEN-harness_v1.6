# ADR-0016: Numeric Canonicalization & the 62-Symbol Legal Charset

- **Status:** Accepted 2026-09-07
- **Supersedes:** none
- **Superseded by:** none
- **Related:** ADR-0015 (Entropy Gate, capability whitelist), ADR-0017 (tests as error boundary)

## Context

The C1 service and the C2 SessionEventLog are the audit surfaces of the harness. Every C1 request is logged (or not) via the C2 `on_session_event` callback (`service.py:1145`, `c2_session_event_log/service.py:114`). The audit log is a sequence of `SessionEvent(payload=dict)` records — a dict of arbitrary JSON-serializable values.

A user-supplied payload (a chat message, a `secrets.put` value, a `model.patch` body) flows into the audit log as bytes. The audit log is the surface where injection lives:

- A chat message containing a Unicode control character (e.g. `U+202E` right-to-left override) gets rendered in a downstream tool as code and confuses the reader.
- A `secrets.put` value containing a newline gets split across two lines in a text-only audit viewer.
- A model name containing a `\x00` byte truncates the entry in a C-style viewer.

The 256-byte space is not auditable. An agent cannot enumerate every possible byte combination and classify its risk. The user input surface is fundamentally open (the user can type any character), but the *audit-render* surface must be bounded.

The v0x04 envelope (ADR-0014) already canonicalizes the secret-storage path: every secret is `bytes`, sealed through `seal(..., name=name)`, stored as a base64 envelope. The seal is lossless and the storage is canonical. The open problem is the *audit-render* surface, not the *storage* surface.

## Decision

The **Number Gate** is the canonicalization layer that renders any byte sequence as a number string in the audit surface. The **62-symbol legal charset** is the projection that the audit log uses for text rendering.

### The principle: lossless numbers, projected text

1. **All data is canonicalized to numbers.** Bytes → integer codes. A 1 KB payload is a 1024-element list of integers, one per byte. No information is lost at this step.
2. **Text is a filtered projection of numbers.** The legal text alphabet is the 62 codes `[A-Z a-z 0-9]`. The render function maps a code to a character if it is in the legal alphabet, and to a numeric escape token `#0xNN` otherwise.
3. **The filter applies at the audit boundary, never at the user-input boundary.** A user typing `Hello, world!` gets exactly that in the in-memory `SessionEvent.payload`. The audit log gets `Hello, world!` (comma and exclamation pass the filter because they are *rendered as escape tokens* — wait, comma and exclamation are NOT in the 62 codes). Let me restate: the filter at the audit boundary REPLACES non-legal characters with `#0xNN` escape tokens. The in-memory payload is lossless. The audit-log-rendered text is the 62-code projection.
4. **Original bytes are reconstructed only at the provider boundary.** When the harness hands a secret to a provider, the seal is `open_envelope(...)` which returns the original bytes. The bytes flow to the provider's HTTP request body and are not re-rendered.

### The 62-code legal alphabet

```python
LEGAL_TEXT_CODES = frozenset(
    range(0x30, 0x3A)  # 0-9
  | range(0x41, 0x5B)  # A-Z
  | range(0x61, 0x7B)  # a-z
)
# Size: 10 + 26 + 26 = 62
```

These are the 62 codes that the audit-render function passes through unchanged. The other 194 codes (out of 256) are replaced with the escape token `#0xNN` where `NN` is the uppercase hex of the code.

### The render function

```python
def to_codes(data: bytes) -> list[int]:
    """Lossless: every byte becomes one integer code (0–255)."""
    return list(data)

def from_codes(codes, *, strict: bool = True) -> str:
    """Project codes to text.
    
    strict=True (audit render): non-legal codes become '#0xNN' escapes.
    strict=False (provider boundary): reconstruct the exact original bytes.
    """
```

The `strict=False` path is used at the provider boundary, where the harness hands a secret's bytes to the LLM provider's HTTP request body. The original bytes are reconstructed exactly. The audit log never sees this; the audit log only ever sees the `strict=True` projection.

### Where the filter applies (and where it does NOT)

| Surface | Filter? | Reason |
|---|---|---|
| C1 request body (incoming) | **No** | User input. Lossless. |
| C1 in-memory handler state | **No** | In-process, never rendered. |
| `SessionEvent.payload` (in-memory) | **No** | Lossless. |
| Audit log emit (`on_session_event`) | **Yes** | Render boundary. 62-code projection. |
| `secrets.log` envelope storage | **No** | Already v0x04 canonical, base64 sealed. |
| Provider HTTP request body | **No** | `from_codes(strict=False)` reconstructs exact bytes. |
| Provider response body (SSE stream) | **No** | Provider output, not a user-controlled surface. |
| Console / `logger.warning` | **Yes** | Render boundary. Same filter as the audit log. |
| `docs/entropy-map.md` | **Yes** | Render boundary. The doc is a human-readable projection. |

The principle: **the filter is a render filter, not a storage rewrite.** Storage stays lossless. Rendering is bounded to 62 codes.

### The one real tension (and its resolution)

**API keys and secrets are not `[A-Za-z0-9]`.** A key like `sk-proj-abc_-` contains `-` and `_`. The 62-code filter would render the `-` and `_` as `#0x2D` and `#0x5F` escapes, which is correct for the *audit log* (we want a text-only log to show the escape), but would break the *secret's value* if applied at the wrong boundary.

**Resolution:** the filter applies only at the *render* boundary. The secret's value is stored losslessly through the v0x04 envelope (base64 sealed, no projection). The audit log gets the projected view (`sk#0x2Dproj#0x2Dabc#0x5F#0x5F`). The provider gets the exact bytes (`sk-proj-abc__`) at the provider boundary.

This is the same resolution as the v1.4.0 attachment story (ADR-0018 deferred): untrusted binary input flows through the envelope losslessly and is rendered safely at the audit boundary.

## Consequences

### Positive

- **Bounded audit surface.** The audit log can only contain 62 character codes + the `#0xNN` escape syntax. An agent can enumerate the entire space (256 codes) and classify the risk of each. Infinite chaos becomes a closed set.
- **Lossless storage.** Secrets, attachments, and chat content are stored without re-encoding. The seal is `bytes`; the v0x04 envelope is `bytes`; the JSONL log is `bytes` in a base64 envelope.
- **Provider boundary is exact.** `from_codes(strict=False)` reconstructs the original bytes at the provider HTTP request. No byte is changed between the user's input and the LLM's input.
- **Audit-render is a single function.** `from_codes(strict=True)` is the only place the filter lives. Every audit emit calls it. There is no way to skip it.

### Neutral

- The escape token `#0xNN` is a 5-character projection of a 1-byte code. Audit logs grow by ~4× in the worst case (every byte is non-legal). For a typical chat message, the growth is < 2× because most bytes are ASCII alphanumeric + a few escape tokens.
- The 62-code alphabet is fixed. Adding a code (e.g. space) requires a new ADR. Removing a code also requires a new ADR. The set is frozen at the v1.4.0 release.
- The `LEGAL_TEXT_CODES` constant is exported. The test suite and the Entropy Map both reference it. A future change to the alphabet is detected by the invariant checker.

### Negative

- **The filter is one place to forget.** A future maintainer who calls `from_codes(strict=False)` in an audit-render context would leak raw bytes into the audit log. The invariant `audit emit calls strict=True` (checked by `scripts/invariants_check.ps1`) catches this regression.
- **The 62-code alphabet is a policy choice.** Some downstream tools (e.g. C-style viewers) handle `#0xNN` correctly; others do not. The v1.4.0 audit log is intended for the C2 SessionEventLog + a human-readable audit viewer. If a future downstream consumer cannot handle the escape syntax, that is its problem; the projection is the canonical view.
- **User-visible rendering may look "ugly" with escape tokens.** A chat message with emoji is rendered as `#0xF0#0x9F#0x98#0x80` in the audit log. This is correct (emoji is not in the 62 codes) and intentional (the audit log is a security artifact, not a user-facing display).

## Wire format (unchanged)

No wire-format change. The filter is a render-time function; the wire bytes are unaffected. The audit log entries gain a new optional field `audit_text: str` in v1.4.0 (the 62-code projection of the payload). Existing log readers ignore the new field. The original `payload` field stays lossless.

## Test plan

6 new tests in `tests/cordis/test_number_gate.py`:

1. `test_legal_text_codes_is_62_codes` — `len(LEGAL_TEXT_CODES) == 62`. (Static shape.)
2. `test_all_256_byte_codes` — parametrize over `range(256)`, assert exactly the 62 legal codes pass as text, the other 194 are escaped. (Generator-emitted; one parametrized test, 256 cases.)
3. `test_legal_roundtrip_lossless` — `from_codes(to_codes(x), strict=False) == x` for arbitrary bytes (a 1-byte, 16-byte, 256-byte, 4096-byte, and 65536-byte payload). (5 cases.)
4. `test_render_strict_replaces_non_legal_with_escape` — a payload with non-ASCII bytes renders with `#0xNN` escapes under `strict=True` and reconstructs exactly under `strict=False`. (1 test, 3 cases.)
5. `test_audit_emit_uses_strict_true` — a synthetic `SessionEvent` whose payload contains a non-legal byte is rendered as the strict projection in the audit log. (1 test.)
6. `test_provider_boundary_uses_strict_false` — a synthetic secret value containing `-` and `_` is reconstructed exactly at the provider boundary. (1 test.)

## Rejected alternatives

- **Filter at user-input boundary (e.g. reject chat messages with non-ASCII).** Rejected: user-visible UX regression, no security benefit. The user-input boundary is fundamentally open; trying to bound it at the input layer breaks real workloads.
- **Filter at the storage layer (e.g. base64 the secret value before sealing).** Rejected: the v0x04 envelope already canonicalizes. Adding a second canonicalization at storage time is redundant and breaks the provider boundary.
- **Allow all 256 codes at the audit-render boundary (status quo).** Rejected: that's the unbounded entropy the proposal closes. An agent cannot audit 256 codes; an agent can audit 62.
- **Use a different legal alphabet (e.g. printable ASCII = 95 codes).** Rejected: 95 is a less useful number. The 62-code set is the URL-safe / file-safe / log-safe subset of printable ASCII. It is the most-tested of all printable subsets.
- **Project to a different escape syntax (e.g. `\u00NN`).** Rejected: `#0xNN` is the same syntax as the v1.3.x envelope extension encoding (`struct.pack(">HH")`). The syntax is consistent with the rest of the codebase.

## Implementation notes

- `src/dhc/cordis/number_gate.py` — the `LEGAL_TEXT_CODES` constant, the `to_codes` and `from_codes` functions, the `render_audit_text` helper.
- The audit emit is a single function in `c2_session_event_log/service.py` (or a helper in `cordis/number_gate.py`) that takes a `SessionEvent.payload` and emits the projected text. Every C2 event listener calls this function before appending to the log.
- The provider boundary is in the C7 LLM stream adapter: `from_codes(strict=False)` reconstructs the secret bytes before they are passed to the provider's HTTP request body.
- The invariant `audit emit calls strict=True` is checked by `scripts/invariants_check.ps1` against the source code (regex match for `from_codes(strict=False)` outside the C7 adapter).
