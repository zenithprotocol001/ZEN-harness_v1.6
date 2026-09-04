# ADR-0014: Canonical MAC Input (v0x04 envelope, length-prefixed fields)

- **Status:** Accepted 2026-09-06
- **Supersedes:** none
- **Superseded by:** none
- **Related:** ADR-0012 (v0x03 envelope), ADR-0013 (name binding)

## Context

ADR-0013 (v1.3.3) bound the secret name into the v0x03 MAC input
to close the confused-deputy attack where an attacker with write
access to the secrets log could store a valid v0x03 envelope
under a different name. The v0x03 MAC input is

```
tag = HMAC(K_mac, header ‖ ext_area ‖ nonce ‖ name ‖ ct)
```

The `name ‖ ct` boundary in this input is **not canonical**.
Both `name` and `ct` are variable-length and adjacent in the
concatenation. An attacker who controls both (and they do: they
control the `ct` they wrote, and v1.4.0 will let them write
arbitrary binary as `ct` for attachments) can shift bytes across
the boundary without changing the concatenated byte string:

```
legit:  name=b"A"  ct=b"BC"  →  segment  b"A" ‖ b"BC" = b"ABC"
forged: name=b"AB" ct=b"C"   →  segment  b"AB" ‖ b"C"  = b"ABC"
```

Same MAC input → same tag → MAC verifies. The attacker can re-open
the envelope with the new name as long as the new name is a
byte-prefix of `original_name ‖ ct`.

This is a **latent forgery primitive**. The exploit only fires
when the target name is a byte-prefix of `(original name ‖ ct)`,
so the immediate blast radius is narrow. But v1.4.0 attachments
will make `ct` large, attacker-controlled bytes; the attacker can
then construct a name + ct pair that satisfies the prefix
condition for any target name they want. The envelope's encoding
flaw becomes the attachment pipeline's flaw.

A v1.3.3 audit on 2026-09-05 surfaced this as Finding 1 and
recommended ADR-0014. The fix must land before v1.4.0 ships
attachments through this envelope.

## Decision

Introduce a new envelope version, **v0x04 (`DHC4`)**, with a
**canonical MAC input** that length-prefixes the `name` field
with a 32-bit big-endian length:

```
mac_input = header ‖ ext_area ‖ nonce ‖ struct.pack(">I", len(name)) ‖ name
tag       = HMAC-SHA256(K_mac, mac_input ‖ ct)
```

The v0x04 wire format on disk is **byte-for-byte identical** to
v0x03:

```
HEADER   (4)   b"DHC4"
EXT_TOTAL (2)  uint16 BE, total extension bytes
EXTS      (n)  length-prefixed extension blocks:
                [ext_len:2 BE][ext_type:2 BE][ext_body:ext_len]
NONCE   (16)  AEAD nonce (per envelope)
CT       (m)  ciphertext
TAG     (32)  HMAC-SHA256 tag
```

The only change versus v0x03 is the **MAC input rule** at seal
and open time. The on-disk bytes are the same. The 4-byte header
distinguishes which rule the reader applies.

### v0x03 is now legacy read-only

- `seal()` writes v0x04 by default.
- `open_envelope` dispatches on the 4-byte header:
  - `DHC1` (v0x01): v1.2.0/v1.3.0 fixed-salt envelopes. Read-only.
  - `DHC2` (v0x02): v1.3.1 per-envelope-nonce envelopes. Read-only.
  - `DHC3` (v0x03): v1.3.2/v1.3.3 envelopes. **Read-only with v0x03
    MAC input rule** (i.e. `header ‖ ext_area ‖ nonce ‖ name ‖ ct`,
    length-unprefixed). New writes go to v0x04.
  - `DHC4` (v0x04): v1.3.4+ envelopes with canonical MAC input.
- Migration script (`scripts/migrate_v03_to_v04.py`) reads v0x03
  entries and re-seals them as v0x04. See "Migration" below.

### Why length-prefix only `name` (not `ext_area`, not `ct`)

The MAC input is `header ‖ ext_area ‖ nonce ‖ len(name):u32 ‖ name ‖ ct`.

- `header` is fixed-length (4 bytes). No ambiguity.
- `ext_area` is self-describing: it starts with `EXT_TOTAL:u16`,
  so the reader can compute `ext_area_end = 2 + ext_total`. The
  boundary between `ext_area` and `nonce` is unambiguous. No
  length prefix needed.
- `nonce` is fixed-length (16 bytes). No ambiguity.
- `name` is variable-length and adjacent to `ct` (also
  variable-length). **This is the seam** that ADR-0013 introduced
  and ADR-0014 fixes. Length-prefix `name` with `u32 BE` and the
  boundary becomes unambiguous.
- `ct` is the last field in the MAC input. The reader knows its
  length from the envelope: `ct_len = len(envelope) - body_start -
  TAG_LEN`. The boundary between `ct` and the end of the MAC input
  is unambiguous. No length prefix needed.

Adding a length prefix to `ext_area` or `ct` would be
belt-and-suspenders; it adds 4 bytes to the MAC input but no
security benefit. ADR-0014 keeps the change minimal.

### Big-endian

`u32 BE` matches the rest of the wire format (which uses `>H`,
`>I` throughout). The 32-bit width allows secret names up to 4 GiB,
which is well above any realistic limit (the production path uses
short names like `llm_provider_openai_gpt-4o`).

## Migration

A v1.3.4 ship includes `scripts/migrate_v03_to_v04.py`, a
one-time migration tool that:

1. Reads each entry in `secrets.log` (JSONL).
2. For each `set` entry, attempts to open the envelope with
   `open_envelope(blob, key, name=name, require_binding=False)`.
3. **Refuses to migrate envelopes where `name == b""`**
   (anonymous v0x03 envelopes are a downgrade vector — see
   ADR-0013 §"Primitives path"). The migration raises
   `SecretEnvelopeError("refusing to migrate anonymous v0x03
   envelope: ...)` and exits with a non-zero code.
4. Re-seals via `seal(value, key, name=name)`. The seal writes
   v0x04 (canonical MAC input).
5. Writes a new `secrets.log` atomically (tmp + rename). The old
   log is preserved at `<dir>/secrets.log.v03.bak`.
6. Idempotent: running twice is a no-op (no v0x03 entries remain
   after the first run).

The migration is **opt-in**. Users who have no v0x03 envelopes
(v1.3.3 just shipped and v0x04 is the new default) do not need
to run it. The DoD does not run the migration by default; it
runs only when a `--migrate` flag is passed.

In practice, because v1.3.3 had no production deployments
(it shipped one day before v1.3.4), no migration is required
for any real user. The script exists for completeness and to
exercise the migration path in tests.

## Consequences

### Positive

- Field-boundary shift forgeries on the v0x04 envelope are
  impossible. The `len(name):u32` prefix disambiguates the
  boundary, so any byte shift between `name` and `ct` changes
  the canonical MAC input and fails the tag check.
- v1.4.0 attachments can safely seal through the v0x04 envelope
  because the encoding is canonical.
- v0x03 envelopes remain readable (read-only) for backward
  compatibility with the v1.3.3 test artifacts and any user
  with v0x03 envelopes on disk.
- The wire format is byte-for-byte identical to v0x03, so the
  parser is shared.

### Neutral

- v0x01 and v0x02 envelopes are unchanged. Their MAC inputs do
  not include the name; the field-boundary shift does not apply
  to them.
- The v1.3.3 test `test_v3_unknown_extension_type_is_preserved`
  is unchanged. The forward-compat extension `0x0002` placeholder
  still works in v0x04 (the parser doesn't reject unknown types).

### Negative

- Two envelope versions are now read-only (`DHC1`, `DHC2`); one
  is read-only with v0x03 MAC rule (`DHC3`); one is canonical
  (`DHC4`). The reader has more code paths. This is the cost of
  the version bump.
- A future v0x05 would also be a new version. Versioning every
  MAC input change is a precedent. The alternative (silent
  in-place changes) is worse: the reader cannot tell which MAC
  input rule applies, and silent breaks are impossible to debug.

## Wire format (canonical)

The v0x04 envelope is identical to v0x03 on disk:

```
HEADER   (4)   b"DHC4"
EXT_TOTAL (2)  uint16 BE, total extension bytes
EXTS      (n)  length-prefixed extension blocks
NONCE   (16)  AEAD nonce (per envelope)
CT       (m)  ciphertext
TAG     (32)  HMAC-SHA256 tag
```

The v0x04 MAC input rule is:

```
mac_input = header ‖ ext_area ‖ nonce ‖ struct.pack(">I", len(name)) ‖ name
tag       = HMAC-SHA256(K_mac, mac_input ‖ ct)
```

## Test plan

8 new tests in `tests/chat/test_secrets_v4.py`:

1. `test_v04_aad_roundtrip_preserves_plaintext` — v0x04 seal + open
   with the same name recovers the plaintext, including at HMAC
   block boundaries.
2. `test_v04_field_boundary_shift_fails` — the headline test.
   Seal a v0x04 envelope with `name=b"A"` and plaintext `b"BC"`.
   Attempt to open it with `name=b"AB"` and a corresponding
   "truncated" ciphertext (or no ciphertext change — depends on
   the forgery setup). The MAC must fail because the canonical
   MAC input has `len(name):u32 = 0x00000002` for `name="A"`,
   not `len(name):u32 = 0x00000003` for `name="AB"`. Either
   way, the open raises.
3. `test_v03_envelope_still_readable` — a v0x03 envelope written
   by v1.3.3 is readable by the v0x04 reader with the
   non-canonical MAC rule.
4. `test_v02_v01_envelopes_still_readable` — backward compat.
5. `test_v04_default_seal_path` — the production path through
   `SecretsService.put_raw` / `get_raw` writes v0x04 (`DHC4`)
   by default.
6. `test_migrate_refuses_anonymous_v03_envelope` — the migration
   script refuses a v0x03 envelope that was sealed with
   `name=b""` (downgrade vector).
7. `test_v04_name_length_prefix_is_big_endian_u32` — the prefix
   is `>I` (4 bytes, big-endian), not native byte order.
8. `test_two_v04_envelopes_same_name_different_ciphertexts` —
   two v0x04 envelopes of the same plaintext and same name
   produce different ciphertexts (KDF salt and AEAD nonce are
   random per seal).

4 new tests in `tests/chat/test_migrate_v03_to_v04.py`:

1. `test_migrate_v03_to_v04_success` — a v0x03 envelope
   (non-anonymous) is migrated to v0x04. The new log has the
   same names; the envelopes are now `DHC4`.
2. `test_migrate_refuses_anonymous_v03` — anonymous v0x03
   envelopes cause the migration to raise.
3. `test_migrate_is_idempotent` — running the migration twice
   leaves the log in the same state (no v0x03 envelopes remain
   after the first run).
4. `test_migrate_preserves_old_log` — the original log is
   preserved at `secrets.log.v03.bak`.

## Rejected alternatives

- **In-place fix within v0x03.** Rejected: changing only the MAC
  input rule within v0x03 changes the tag for identical content.
  The reader cannot tell which rule to apply without an in-band
  flag (e.g. an extension type), and in-band flags are fragile
  (a reader that doesn't know about the flag will compute the
  wrong MAC input). A new version is the only clean disambiguation.
- **Add `len(name):u32` to v0x03 in place and let the migration
  re-sign old envelopes.** Same as above; rejected for the same
  reason.
- **Use a standard AEAD with a real `aad=` parameter.** Rejected:
  the runtime does not have the optional `cryptography`
  dependency (see `secrets.py` module docstring). Switching to
  a standard AEAD is a larger refactor and out of scope for
  v1.3.4. The length-prefix approach achieves the same security
  property (canonical encoding) without changing the underlying
  primitive.
- **Length-prefix every variable-length field (`ext_area`, `name`,
  `ct`).** Rejected: belt-and-suspenders. `ext_area` is
  self-describing (starts with its own length); `ct` is the last
  field and the reader knows its length from the envelope. Only
  `name ‖ ct` has the canonicalization problem. Adding length
  prefixes to the other fields adds 8 bytes to the MAC input
  with no security benefit.
- **Skip v1.3.4 and accept the risk.** Rejected: v1.4.0
  attachments will feed arbitrary binary through this envelope,
  making the latent forgery exploitable. The fix is small (~8
  tests, one new envelope version, one migration script) and
  blocking on it is cheaper than carrying the bug forward.
