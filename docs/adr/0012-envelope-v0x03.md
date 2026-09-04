# ADR-0012: v0x03 Envelope — Extension Blocks and Strict Per-Secret Nonce

- Status: Accepted (v1.3.2)
- Date: 2026-09-04
- Supersedes: none (extends ADR-0010)
- Extends: `docs/secrets-model.md`

## Context

v1.3.0 introduced the v0x01 envelope (`DHC1` header, fixed scrypt salt) and
v1.3.1 introduced v0x02 (`DHC2` header, per-envelope nonce as the scrypt KDF
salt). Both versions share the 16-byte AEAD nonce field that follows the
header, and the same `nonce (16) || ct (n) || tag (32)` layout.

v1.3.1's per-secret nonce was a strict upgrade over v0x01, but the v0x02 KDF
salt is a single random value: 16 bytes. To be conservative against offline
brute-force on a stolen vault, we want **two** distinct per-secret random
values: one for the KDF and one for AEAD, both random per secret.

We also need a way to attach future per-secret metadata (e.g. a creation
timestamp, a vault version tag, a per-secret HMAC of the secret name) without
breaking the wire format. The existing `nonce (16) || ct (n) || tag (32)` layout
has no room for additional structured fields.

The constraint: every v0x01 and v0x02 envelope must remain readable. No
migration script. Old `secrets/*.bin` files stay on disk and are decrypted in
place.

## Decision

Introduce a third envelope version, v0x03 (`DHC3` header), with:

1. **Extension blocks** between the header and the AEAD nonce. Each extension
   is `[ext_len:2 BE][ext_type:2 BE][ext_body:ext_len]`. The total extensions
   area is itself length-prefixed with `[ext_total_len:2 BE]` (so the AEAD
   nonce position is computed at parse time, not hard-coded).

2. **Strict per-secret nonce** as the first extension, type `0x0001`, body
   = 12 random bytes. The KDF scrypt salt becomes
   `strict_nonce (12) || aead_nonce (16)` = 28 bytes. The AEAD nonce field is
   unchanged in length (still 16 bytes).

3. **Forward-compat extension parsing**: unknown extension types are silently
   skipped, not rejected. The parser walks the extension list, picks up
   recognized types (currently only `0x0001`), and ignores the rest. This lets
   v1.3.3+ add new extension types without breaking v1.3.2 readers.

## Wire format

v0x03 envelope (all big-endian):

```
HEADER       (4)   b"DHC3"            (vs b"DHC1" / b"DHC2")
EXT_TOTAL    (2)   uint16, total extension bytes
EXT_0_LEN    (2)   uint16, length of ext_0 body
EXT_0_TYPE   (2)   uint16, 0x0001 for the strict nonce
EXT_0_BODY   (12)  os.urandom(12)     (strict per-secret nonce)
AEAD_NONCE  (16)   os.urandom(16)     (per-envelope AEAD nonce, unchanged)
CT          (n)    ciphertext
TAG        (32)    GCM auth tag
```

For v1.3.2 the only extension is `0x0001` (strict nonce), so
`EXT_TOTAL == 16` and `EXT_0_LEN == 12`. Future versions may add more.

## Salt strategy

| Version | Header | KDF salt | AEAD nonce |
|---|---|---|---|
| v0x01 | `DHC1` | `b"dhc-secrets-v1"` (global) | 16 random bytes per secret |
| v0x02 | `DHC2` | 16-byte AEAD nonce as scrypt salt (per-secret) | 16 random bytes per secret |
| v0x03 | `DHC3` | `strict_nonce (12) || aead_nonce (16)` = 28 bytes (per-secret, two distinct random values) | 16 random bytes per secret |

v0x03 gives the strongest offline-brute-force posture: a stolen vault file
yields no static salt (the KDF salt is freshly random per secret) AND the
strict nonce is independent of the AEAD nonce (so a future key-recovery
attack on the AEAD nonce field doesn't immediately reveal the KDF salt).

## Compatibility

- `open_envelope()` dispatches on the 4-byte header. v0x01 and v0x02 are read
  with their existing KDF / AEAD logic, untouched.
- `seal()` defaults to v0x03 when called without a header argument. Existing
  callers (e.g. `SecretsService.put_raw`) that don't pass a header now write
  v0x03. This is forward-only: the disk gains v0x03 entries on next write;
  v0x01 / v0x02 entries are not touched.
- No migration script. The v1.3.2 release notes acknowledge that a fresh
  write produces v0x03; pre-existing v0x01/v0x02 envelopes are read normally
  and will be re-sealed as v0x03 the next time the secret is updated.

## Implementation outline (v1.3.2)

- `src/dhc/cordis/secrets.py`:
  - Add `HEADER_V3 = b"DHC3"`, `__all__ += ["HEADER_V3", "StrictNonceError"]`.
  - Add `_pack_extension(ext_type: int, body: bytes) -> bytes` returning
    `struct.pack(">HH", len(body), ext_type) + body`.
  - Add `_serialize_extensions(extensions: list[tuple[int, bytes]]) -> bytes`
    that prefixes the concatenation with the total length.
  - Add `_parse_extensions(buf: bytes) -> dict[int, bytes]` that walks the
    length-prefixed list and returns a dict keyed by extension type. Unknown
    types are returned in the dict (caller decides).
  - Extend `_seal_with(plaintext, master_key, header, *, extensions=None)` to
    write the extension block before the AEAD nonce for v0x03.
  - Extend `open_envelope` to dispatch on v0x03: read `EXT_TOTAL`, skip past
    the extension area, then read the 16-byte AEAD nonce, then read the
    ciphertext and tag.
  - `seal()` accepts an optional `extensions=` kwarg; if `None` and the
    header is `DHC3`, generate a fresh `os.urandom(12)` and pack it as the
    `0x0001` extension.
- `tests/chat/test_secrets_v3.py` (new, 8 tests):
  1. v0x03 round-trip preserves plaintext
  2. v0x03 extension is present and 12 bytes (`len(ext_0x0001) == 12`)
  3. two v0x03 envelopes of the same plaintext produce different strict nonces
  4. v0x03 ciphertext differs from v0x02 ciphertext for the same plaintext
     (proves the strict nonce is part of the KDF salt path)
  5. tampering with the extension bytes fails the open (GCM auth tag check)
  6. v0x01 (DHC1) is still readable
  7. v0x02 (DHC2) is still readable
  8. unknown extension type (e.g. `0x0002`) is preserved through round-trip
     (forward compat)
- `tests/chat/test_secrets.py`: update the round-trip helper to accept any of
  the three headers; add a parametrized fixture covering `DHC1` / `DHC2` /
  `DHC3`.

## Consequences

- v0x03 envelopes are 18 bytes longer than v0x01/v0x02 (12-byte strict nonce
  + 4-byte extension header + 2-byte ext_total). For 100 secrets of ~64 bytes
  ciphertext, the on-disk size grows by 1.8 KB — negligible.
- The `_derive_keys(master, salt)` signature does not change. The v0x03 salt
  is the 28-byte concatenation, but it's still a single argument.
- `SecretsService.put_raw` callers that want to write a v0x01 envelope for
  some legacy reason can still pass `header=HEADER_V1`. Documented in
  `docs/secrets-model.md` and the `secrets.py` module docstring.
- Future v1.3.3+ may add extension types (e.g. `0x0002` for a creation
  timestamp). The wire format and parser are ready; the only work is a new
  extension-type constant and a handler.

## Alternatives considered

1. **Layout bump, not extension blocks.** Move the 12-byte strict nonce to a
   fixed position in the layout (e.g. `HEADER || strict_nonce || aead_nonce
   || ct || tag`). Simpler parser, but breaks every tool that hard-codes
   offsets. Rejected — extension blocks give the same security property with
   no offset hard-codings.

2. **Per-secret HMAC of the secret name.** Considered as extension `0x0002`
   for v1.3.2. Deferred to v1.3.3: requires a name-binding spec change and
   touches `SecretsService` directly. Out of scope for v1.3.2.

3. **Header byte `DHC3` reserved but no extension support.** Tempting because
   it's the smallest change, but it doesn't give the "two distinct per-secret
   random values" property. Rejected.

4. **Argon2id instead of scrypt.** Considered for the KDF upgrade. Deferred:
   the v1.3.x family uses scrypt; switching KDF is a separate ADR.
