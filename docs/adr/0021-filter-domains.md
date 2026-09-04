# ADR-0021: Filter domains (where the 62-gate applies, where it does not)

- **Status:** accepted
- **Date:** 2026-09-08
- **Back-references:** ADR-0016 (Number Gate), ADR-0018
  (prompt-path control), ADR-0020 (attachments)

## Context

The 62-gate is a projection from a 256-byte code space to a
62-code text alphabet. It is *not* a content sanitizer; it is
a render boundary. The v1.4.0 ADRs scattered the
"applies here / does not apply here" rule across three
documents (ADR-0015 §"Tradeoffs", ADR-0016 §"Principles",
and the audit-surface table in `docs/entropy-map.md`).
v1.5.0 needs a single document that names the four
*domains* of byte flow in the system and declares the
filter rule for each.

Without this ADR, future work on attachments (ADR-0020)
and branching (ADR-0019) will repeatedly re-derive the
filter rule from first principles, with the real risk
that a binary attachment path picks up a 62-filter call
by accident.

## Decision

There are four filter domains. Each domain has a *rule*
and a *projection function*.

### Domain 1: `audit-text`

- **Rule:** 62-code filter, escape non-legal with `#0xNN`.
- **Projection:** `render_audit_text(bytes|str) -> str`
  (from `src/dhc/cordis/number_gate.py`).
- **Where it applies:**
  - C2 SessionEventLog emit (`on_session_event`).
  - `docs/entropy-map.md` render.
  - The `audit_text` field of every audit event.
  - Secret *names* (the names of API keys, model configs).
  - Capability names (the `Capability.value` strings).
  - File paths in audit log entries.
  - Plugin manifest fields.
- **Why:** the audit log is read by a human or by an
  agent. Both expect a 62-code text alphabet. The
  projection is a render choice; storage stays lossless
  (the original bytes live in `SessionEvent.payload`,
  not in `audit_text`).

### Domain 2: `asset`

- **Rule:** no filter. Bytes round-trip exactly.
- **Projection:** none. The bytes are the bytes.
- **Where it applies:**
  - Out-of-line attachment binary payloads
    (`~/.dhc/attachments/{session_id}/{uuid}.bin`).
  - The base64-encoded `data:` URI in the web client
    when rendering an `<img>` / `<audio>` / `<embed>`.
  - The v0x04 envelope ciphertext on disk.
  - The `SessionEvent.payload` field of an audit event
    (lossless in-memory).
- **Why:** an image is a sequence of bytes. Filtering
  them would corrupt the preview. The `asset` domain is
  *machine-to-machine*: a renderer (browser, audio
  player, PDF viewer) interprets the bytes, and the
  62-gate would only interfere.

### Domain 3: `provider-bytes`

- **Rule:** no filter at the storage boundary. If the
  bytes must cross a code boundary that is *not*
  lossless (e.g. a future JSONL log or a wire format
  that uses a smaller alphabet), the projection is
  `from_codes(strict=False)` (lossless bytes
  reconstruction).
- **Projection:** `render_provider_bytes(bytes|str) -> bytes`
  (from `src/dhc/cordis/number_gate.py`).
- **Where it applies:**
  - The HTTP request body sent to OpenAI / Anthropic /
    OpenRouter. The v0x04 envelope's `open_envelope`
    returns the original plaintext; that plaintext is
    the value the provider sees.
  - The API key passed to a provider client. The key
    is sealed in a v0x04 envelope, opened with the
    master key, and the original bytes go to the
    provider.
- **Why:** the provider is a machine-to-machine boundary,
  but the machine on the other side (the LLM) handles
  any UTF-8. The `provider-bytes` domain is the only
  one where the *storage* is lossless AND the *wire*
  is lossless.

### Domain 4: `prompt-text`

- **Rule:** no filter. Bytes pass unchanged from user
  input to provider HTTP body.
- **Projection:** none. The bytes are the bytes.
- **Where it applies:**
  - The user's `content` field in
    `POST /api/sessions/{sid}/messages`.
  - The text of the WebSocket `chat.send` frame.
  - The C3 prompt assembler's user section.
  - The `messages` list passed to
    `LLMStreamAdapter.chat_stream(...)`.
- **Why:** per ADR-0018, the prompt path is governed by
  a *different* control (C3 boundary-token escape + C9
  agent-tool authorization + length cap + usage totals).
  The 62-gate is not appropriate here: it would destroy
  punctuation, and a character-level filter does not
  stop semantic injection.

## The domain table

The four domains form a table. Every byte flow in the
system is in exactly one row.

| Domain | Rule | Where |
|---|---|---|
| `audit-text` | 62-gate, escape | C2 log emit, Entropy Map, secret names, capability names, file paths, manifest fields |
| `asset` | no filter | attachment binaries, `data:` URIs, envelope ciphertext on disk, `SessionEvent.payload` |
| `provider-bytes` | lossless via `from_codes(strict=False)` if needed | provider HTTP body, API key bytes |
| `prompt-text` | no filter | user message content, chat.send WS frame, C3 user section |

## How a future change determines the domain

The rule is: *find the consumer of the bytes.*

- A human or a 62-code-reading agent reads it →
  `audit-text`.
- A renderer (browser, audio player, PDF viewer) reads
  it as bytes → `asset`.
- An LLM provider reads it as UTF-8 → `provider-bytes`
  or `prompt-text`, depending on whether the bytes are
  a structured request (a system prompt, a config
  blob) or unstructured user content.
- A model reads it as a token sequence (which the model
  is good at) → `prompt-text`.

If the consumer is unclear, the byte flow is
`audit-text`. The 62-gate is the conservative default
because it preserves renderability for a human reviewer
without losing information (the projection is reversible
via `from_codes(strict=False)`).

## Consequences

- `src/dhc/cordis/number_gate.py` exports two
  projection functions: `render_audit_text` (for
  `audit-text`) and `render_provider_bytes` (for
  `provider-bytes`). The `asset` and `prompt-text`
  domains do not need a projection function; the bytes
  are passed through.
- The invariant script asserts: (a) `number_gate` is
  imported only by the `audit-text` paths (C2 log emit
  + Entropy Map generator); (b) the binary attachment
  path does not import `number_gate`; (c) the C3 prompt
  assembler, the C7 dispatch, and the provider clients
  do not import `number_gate`; (d) `lookup_api_key` does
  not import `number_gate`.
- A new audit event type added in a future release
  must declare its domain in the Entropy Map audit
  table. The build script regenerates the table from
  the declared events.
- A new byte flow added in a future release that does
  not fit one of the four domains must be added to the
  table in an amendment to this ADR.
