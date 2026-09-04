# ADR-0020: Attachments on the v0x04 envelope (text/SVG inline, binary out-of-line)

- **Status:** accepted
- **Date:** 2026-09-08
- **Back-references:** ADR-0012 (v0x03 envelope), ADR-0013
  (name binding), ADR-0014 (canonical MAC input / v0x04),
  ADR-0018 (prompt-path control), ADR-0021 (filter domains)

## Context

v1.5.0 needs user file attachments in chat messages: images
for screenshots, audio for voice notes, PDFs for documents,
plain text and markdown for code snippets, and SVG for
diagrams. The wire format for storage is the v0x04 envelope
(ADR-0014): canonical MAC input with `len(name):u32 BE`
prefix; the name-binding property of ADR-0013 carries over.

The high-level design decisions are:

- **Inline vs out-of-line**: the red-team pass before v1.5.0
  raised the real concern that the append-only session
  ledger cannot reclaim bytes. The decision is to *route by
  MIME class*, not by byte size. Text-like content
  (`text/plain`, `text/markdown`, `image/svg+xml`) is
  inline — it is small, structured, and renders in the
  audit log via the 62-gate. Binary content (`image/png`,
  `image/jpeg`, `image/gif`, `image/webp`, `audio/mpeg`,
  `audio/wav`, `application/pdf`) is out-of-line — it can
  be megabytes, must round-trip byte-exact, and is not
  human-readable in an audit log anyway.
- **Storage**: out-of-line attachments are sealed with the
  v0x04 envelope. The name is `attachment:{session_id}:{uuid}`
  where `uuid` is 32 hex chars. The `session_id` is part of
  the name so name-binding (ADR-0013) prevents a ref from
  one session being used in another.
- **Domain**: per ADR-0021, attachments have *two* domains.
  The text content (inlined or the metadata of out-of-line
  attachments) is `audit-text` — the 62-gate projects it
  to the legal alphabet for the audit log. The binary
  bytes of out-of-line attachments are `asset` — they are
  *not* filtered; they round-trip byte-exact through
  `from_codes(strict=False)` if the path ever needs to
  re-encode (it does not — the v0x04 envelope is the
  canonical storage and the bytes are already exact).
- **Size limits**: 10 MB per file, 25 MB per message (sum
  of all inline + out-of-line attachment bytes).

## Decision

### MIME allowlist

| MIME class | Inline / out-of-line | Rationale |
|---|---|---|
| `text/plain` | inline | structured, small, audit-renderable |
| `text/markdown` | inline | structured, small, audit-renderable |
| `image/svg+xml` | inline | XML, small, audit-renderable |
| `image/png` | out-of-line | binary, exact bytes, asset domain |
| `image/jpeg` | out-of-line | binary, exact bytes, asset domain |
| `image/gif` | out-of-line | binary, exact bytes, asset domain |
| `image/webp` | out-of-line | binary, exact bytes, asset domain |
| `audio/mpeg` | out-of-line | binary, exact bytes, asset domain |
| `audio/wav` | out-of-line | binary, exact bytes, asset domain |
| `application/pdf` | out-of-line | binary, exact bytes, asset domain |

Anything outside this list is rejected with `415
Unsupported Media Type` and a
`{"error": "mime_not_allowed", "mime": "..."}` body. The
allowlist is enforced at the C1 route handler before any
bytes are read; a hostile client cannot force a write of an
unknown MIME.

### Size limits

- **10 MB per file.** A `POST /api/sessions/{sid}/attachments`
  body larger than 10 MB is rejected with `413 Payload Too
  Large`. The limit is checked *before* the bytes are
  buffered.
- **25 MB per message.** When a `Message.attachments` list
  is updated (via `POST /api/sessions/{sid}/messages` or
  the equivalent WS path), the total size of all
  attachments is computed. If the sum exceeds 25 MB, the
  request is rejected with `413 Payload Too Large` and a
  `{"error": "message_attachment_cap_exceeded", "limit_bytes": 26214400}` body.

### Inline attachment representation

Inline attachments are stored in the session message
ledger as a list field on the message:

```python
class InlineAttachment:
    mime: str           # one of text/plain, text/markdown, image/svg+xml
    body: str           # decoded as UTF-8 (lossy on invalid sequences; caller-provided)
    sha256: str         # 64 hex chars; integrity check on read
```

The `body` is the original UTF-8 string. The audit log
projects the body through the 62-gate. The on-disk JSON
keeps the body verbatim (lossless in the storage; the
projection is a render-time concern per ADR-0016).

### Out-of-line attachment representation

Out-of-line attachments are stored on disk under
`~/.dhc/attachments/{session_id}/{uuid}.bin` (the bytes)
and `~/.dhc/attachments/{session_id}/{uuid}.json`
(metadata: mime, sha256, size, created_at). The bytes
are wrapped in a v0x04 envelope *around* the on-disk
file, with the envelope name
`attachment:{session_id}:{uuid}`.

The message carries only the *ref*:

```python
class OutOfLineAttachment:
    ref: str            # "attachment:{session_id}:{uuid}"
    mime: str
    sha256: str
    size: int
```

The ref format is parseable but not part of the
`key_lookup` secret-name namespace. A test asserts that
`lookup_api_key` ignores refs that begin with
`attachment:`.

### Routes

Three new C1 routes, all gated:

- `POST /api/sessions/{sid}/attachments` →
  `ATTACHMENT_PUT`. Body: the bytes. Headers: `Content-Type`
  (the MIME), `X-DHC-Session-Id` (the session id, for
  the envelope name). Response: `{"ref": "...",
  "sha256": "...", "size": N, "mime": "..."}`.
- `GET /api/attachments/{ref}` → `ATTACHMENT_GET`. Path
  parameter: the full `attachment:{session_id}:{uuid}`
  ref. Response: the bytes with the original `Content-Type`.
- `DELETE /api/attachments/{ref}` →
  `ATTACHMENT_DELETE`. Response: `204`. Removes both the
  `.bin` and `.json` files; the v0x04 envelope is
  forgotten (no log entry).

### Name binding carries over

The v0x04 envelope used to seal out-of-line attachments
inherits name binding (ADR-0013) and canonical MAC input
(ADR-0014). The name is `attachment:{session_id}:{uuid}`;
the session_id is part of the name. An attacker who can
write to the attachments dir cannot reuse a ref from
session A in session B without breaking the MAC.

### Frontend rendering

- **Inline (text/SVG)**: rendered as `<pre>` for
  `text/plain`, `Markdown` for `text/markdown`, and an
  inline `<svg>` (after `DOMPurify` sanitization) for
  `image/svg+xml`. The 62-gate projection appears in the
  EventsPanel and the audit log; the chat panel shows
  the original string.
- **Out-of-line (binary)**: rendered as a `<img>` /
  `<audio>` / `<embed>` with a `data:` URI. The base64
  payload is the *original* bytes (`asset` domain — no
  62-filter). The audit log carries only the ref + mime +
  size + sha256, all of which are 62-safe.

## What this ADR does NOT do

- It does not introduce per-attachment ACLs. Any caller
  authenticated as the session owner can read all
  attachments of the session. Per-attachment ACLs are a
  v1.6.0+ feature.
- It does not introduce attachment versioning. A
  `PUT /api/sessions/{sid}/attachments` creates a new
  ref; an existing ref is not updated in place.
- It does not introduce attachment deduplication. Two
  uploads of the same bytes produce two refs.
- It does not introduce auto-forwarding of attachments
  to the LLM provider. The provider sees only the
  message text. Auto-forward is a v1.5.1 feature.
- It does not allow `text/html`, `application/zip`, or
  any executable MIME. The allowlist is the contract.

## Consequences

- Three new capabilities added: `ATTACHMENT_PUT`,
  `ATTACHMENT_GET`, `ATTACHMENT_DELETE`. Combined with
  branching, this brings the v1.5.0 capability count
  to 26.
- The session JSON schema gains two new fields:
  `Message.attachments: list[InlineAttachment]` and
  `Message.attachments: list[OutOfLineAttachment]`. For
  v1.5.0 these are *separate* fields (an attachment is
  either inline or out-of-line, not both). A v1.5.1
  follow-up may unify them under a single field with a
  discriminator.
- The on-disk attachments directory is
  `~/.dhc/attachments/`. It is created on first use.
- The web client adds an `<AttachmentsPanel>` for
  upload, preview, and delete. The 62-filter is
  asserted *absent* in the asset path (the negative
  test).
- The invariant script asserts: (a) the MIME allowlist
  is exactly the 10 values in the table above; (b)
  size limits are exactly 10 MB and 25 MB; (c) the
  `attachment:` namespace is invisible to `key_lookup`;
  (d) `number_gate` is *not* imported by the binary
  attachment path.
