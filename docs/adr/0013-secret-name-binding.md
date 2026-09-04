# ADR-0013: Secret Name Binding (v0x03 envelopes)

- **Status:** Accepted 2026-09-05
- **Supersedes:** none
- **Superseded by:** none
- **Related:** ADR-0010 (per-secret nonce), ADR-0012 (v0x03 envelope)

## Context

The v1.3.2 secrets envelope (`DHC3`, see ADR-0012) binds the
**plaintext** of a secret to its envelope via an HMAC-SHA256 tag
computed over `header ‖ ext_area ‖ nonce ‖ ciphertext`. The tag
authenticates that the bytes on disk were produced by the legitimate
`seal()` call.

The tag does **not** authenticate the **name** under which the
ciphertext is stored. As a result, an attacker who can write to the
secrets log can mount a *confused deputy* attack:

1. The attacker reads the v0x03 envelope for `name=A` (call it
   `blob_A`).
2. The attacker writes `blob_A` into the log under `name=B` (e.g.
   by deleting the legitimate `name=B` line and appending a `set`
   record whose `blob` field is `blob_A`).
3. When the application later calls `secrets_service.get("B")`, the
   v1.3.2 reader authenticates the tag, decrypts successfully, and
   returns the secret that was originally sealed for `name=A`.
4. If `name=B` corresponds to a different provider (e.g. `B` is
   `llm_provider_anthropic_claude-3-sonnet` but `A` is
   `llm_provider_openai_gpt-4o`), the application will use the
   OpenAI key to authenticate against the Anthropic API. The
   confused deputy fails silently and the wrong key is used.

The threat actor is not the on-disk reader (loopback bind makes that
irrelevant); it is any code path that can mutate the secrets log
file: a misbehaving plugin, a runtime bug, a future debug endpoint,
or a compromise of the calling process. The v0x03 wire format is
ready for an extension-block solution but no binding exists in v1.3.2.

## Decision

Bind the secret name to its envelope by **including the name in the
authenticated MAC input** at seal time and re-supplying it at open
time. The v1.3.3 wire format is **unchanged**: the on-disk byte
layout is identical to v1.3.2. The binding is enforced
cryptographically by the existing 32-byte HMAC-SHA256 tag.

```
tag = HMAC-SHA256(K_mac, header || ext_area || nonce || name || ct)
```

For v0x01 and v0x02 envelopes, the binding is **not** enforced
(they predate this ADR; their tag inputs are unchanged).

### Why MAC-input binding and not extension-block binding

An earlier draft of this ADR proposed using extension type `0x0002`
to carry a separate HMAC of the name. The v1.3.2 envelope is
hand-rolled (HMAC-SHA256 keystream + separate HMAC-SHA256 tag) and
not a standard AEAD primitive, so the "AAD" terminology is used
loosely to mean "extra bytes authenticated by the tag." The
MAC-input approach is strictly stronger than an extension-block
approach because:

- A separate extension would be a new on-disk field that a v0x03
  reader could in principle choose to ignore; the MAC input is
  not optional, the tag is what protects the ciphertext.
- The MAC-input approach costs **zero on-disk bytes**; the
  extension-block approach costs at least 38 bytes per envelope
  (4-byte block header + 32-byte HMAC + 2-byte `ext_total`
  delta). AAD-binding is the cheaper, stronger choice.
- The v0x03 envelope already binds `header` and `ext_area` to the
  tag. Extending the binding to `name` is the same operation one
  level up; no new code path is introduced.

### Production path: fail-closed

`SecretsService.put_raw(name, value)` and
`SecretsService.get_raw(name)` always supply the name to the seal
/ open calls. The v1.3.3 `seal()` raises
`SecretEnvelopeError("name required for v0x03 envelope")` if the
caller does not supply a name and the envelope is v0x03. The
v1.3.3 `open_envelope()` similarly raises if the envelope is v0x03
and no name is supplied.

### Primitives path: opt-out flag for tests and migrations

The low-level `_seal_with()` and `open_envelope()` accept a
`require_binding: bool = True` flag. When `False`, the name is
appended to the MAC input only if non-empty; an empty name is
treated as "no binding." This is the escape hatch for:

- The v1.3.2 test suite (`test_secrets_v3.py` exercises v0x03
  round-trips with `_seal_with` directly, before name-binding was
  introduced). Those tests are updated to pass `name=b"fixture"`
  rather than relying on the opt-out flag.
- Migration scripts that read a v1.3.2-written log and re-seal
  with v1.3.3 binding. A migration uses `require_binding=False`
  on the read side and `seal(value, key, name=...)` on the write
  side.

## Consequences

### Positive

- The confused-deputy attack is closed. Storing `blob_A` under
  `name=B` does not cause a successful decrypt at `name=B`; the
  tag verification fails because the name did not match.
- v0x01 and v0x02 envelopes remain readable (their tags do not
  authenticate the name, by design, because they predate this
  ADR). The reader's behavior for those headers is unchanged.
- The wire format is byte-for-byte identical to v1.3.2. Existing
  on-disk logs do not need to be re-sealed; they will simply be
  re-read with the v1.3.3 reader's name check, which the
  production call path (`SecretsService.get_raw(name)`) supplies
  automatically.
- The `key_lookup.py` chain (`per-model → ___N → per-provider`,
  ADR-0007) is unchanged: every `secrets_service.get(name)` call
  already has a real `name` argument, which now propagates to the
  MAC input at the lowest level.

### Neutral

- The v1.3.2 test `test_v3_unknown_extension_type_is_preserved`
  uses extension type `0x0002` as a forward-compat placeholder.
  v1.3.3 does **not** redefine that type; the extension type
  `0x0002` remains "reserved for forward compat." No collision.
- The v1.3.2 error string for tag mismatch is `"authentication
  failed"`. v1.3.3 reuses the same string for name-binding
  failures to avoid leaking which side of the binding the
  attacker tampered with.

### Negative

- A v1.3.3 reader **cannot** open a v1.3.2-written v0x03 envelope
  *without* a name. The reader's `require_binding=True` default
  will raise. In practice this is fine because (a) the production
  `SecretsService` always supplies the name, and (b) the v1.3.2
  default write path is a v0x03 envelope, but those envelopes
  have no associated key file (`secrets.key` is per-user) and so
  the v1.3.2 test suite and any on-disk v1.3.2 logs are the only
  places where this matters. Tests opt in with `name=b"fixture"`.
- The error message for name-binding mismatch is intentionally
  ambiguous (it does not distinguish "wrong name" from "tampered
  ciphertext" or "tampered tag"). This is a deliberate defense
  against attackers probing the error path to recover the name.

## Wire format (unchanged from v1.3.2)

```
HEADER (4)   b"DHC3"
EXT_TOTAL (2)  uint16 BE, total extension bytes
EXTS     (n)  length-prefixed extension blocks:
                [ext_len:2 BE][ext_type:2 BE][ext_body:ext_len]
NONCE   (16)  AEAD nonce (per envelope)
CT       (m)  ciphertext
TAG     (32)  HMAC-SHA256 tag
```

The only change versus v1.3.2 is **what the tag authenticates**:

```
v1.3.2: tag = HMAC(K_mac, header || ext_area || nonce || ct)
v1.3.3: tag = HMAC(K_mac, header || ext_area || nonce || name || ct)
```

The `name` is the UTF-8 encoding of the secret name (e.g.
`b"llm_provider_openai_gpt-4o"`).

## Test plan

8 new tests in `tests/chat/test_secrets_name_binding.py`:

1. `test_v03_aad_roundtrip_preserves_plaintext` — seal with
   `name=A`, open with `name=A`, plaintext recovered.
2. `test_v03_seal_with_name_a_opens_with_name_a` — lighter
   version of #1, same name on both sides.
3. `test_confused_deputy_name_swap_raises` — **the headline
   test**: seal with `name=A`, open with `name=B` raises
   `SecretEnvelopeError` with `"authentication failed"` in the
   message.
4. `test_v03_open_without_name_raises_unless_require_binding_false`
   — open with `name=""` and `require_binding=True` raises; with
   `require_binding=False` succeeds.
5. `test_two_envelopes_same_name_different_ciphertexts` — two v0x03
   envelopes sealed with the same plaintext and same name produce
   different ciphertexts (KDF salt + AEAD nonce are random per
   seal).
6. `test_tampered_name_or_ciphertext_fails_aead` — flipping a bit
   in the ciphertext raises; flipping a bit in the `name` passed
   to the reader raises.
7. `test_v01_v02_envelopes_still_open_without_binding` — v0x01
   and v0x02 envelopes are readable without a name.
8. `test_v03_default_seal_path_includes_name_in_aad` —
   end-to-end through `SecretsService.put_raw` / `get_raw`,
   confirming the production path passes the name down to the
   MAC input.

## Rejected alternatives

- **Extension block `0x0002` carrying a separate HMAC of the
  name.** Rejected: more on-disk bytes, weaker than MAC-input
  binding, introduces a new code path that a future reader could
  ignore. The MAC input is not optional; an extension is.
- **HMAC the name with a separate subkey `K_bind` derived from
  `K_mac`.** Rejected: no security benefit. The MAC already
  authenticates the input; deriving a subkey for an additional
  hash is a redundancy. The name goes into the existing MAC
  input alongside the existing `header ‖ ext_area ‖ nonce ‖ ct`.
- **Allow the v0x03 reader to silently fall back to "no name
  binding" for unknown v0x03 envelopes.** Rejected: this is
  exactly the confused-deputy attack we are trying to close. The
  default is fail-closed; the only opt-out is the
  `require_binding=False` flag at the primitive level.
- **Use a real AEAD primitive (AES-GCM, ChaCha20-Poly1305) and
  pass `name` as the `aad` parameter.** Rejected: the v1.3.2
  envelope is hand-rolled HMAC-CTR + HMAC-SHA256 tag because the
  runtime does not have the optional `cryptography` dependency
  (see `secrets.py` module docstring). Switching to a standard
  AEAD is a larger refactor and out of scope for v1.3.3. The
  MAC-input binding achieves the same security property
  (authenticate the name) without changing the underlying
  primitive.
- **Use extension type `0x0002` (the existing v1.3.2 test
  placeholder) for the binding.** Rejected: that would collide
  with the v1.3.2 test `test_v3_unknown_extension_type_is_preserved`,
  which uses `0x0002` as a "forward compat" placeholder. v1.3.3
  keeps `0x0002` as a reserved type and uses the MAC input
  instead.

## Implementation notes

- The v1.3.2 `seal()` and `open_envelope()` accept a new
  optional `name` parameter (default `None`). When `name=None` is
  passed to a v0x03 call, the function raises
  `SecretEnvelopeError("name required for v0x03 envelope")`.
- The v1.3.2 `_seal_with()` accepts a new `name: bytes = b""`
  parameter. When non-empty, the name is appended to the v0x03
  MAC input. v0x01 / v0x02 are unchanged.
- `SecretsService.put_raw(name, value)` calls
  `seal(value, key, name=name.encode("utf-8"))`.
- `SecretsService._replay` calls
  `open_envelope(blob, key, name=name.encode("utf-8"))`.
- The existing test `test_seal_wrong_key_rejected` in
  `tests/chat/test_secrets.py` is updated to pass
  `name=b"hello"`. The existing v0x03 tests in
  `tests/chat/test_secrets_v3.py` are updated to pass
  `name=b"fixture"` (or a per-test name). v0x01 / v0x02 tests are
  unchanged because their envelopes do not bind a name.
