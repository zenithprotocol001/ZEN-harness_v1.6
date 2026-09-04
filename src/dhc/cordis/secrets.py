"""dhc.cordis.secrets: Authenticated at-rest envelope for the C2 secret log.

The harness runs on loopback (127.0.0.1) and the threat model for the
C2 secret log is "a casual file-system reader who can see the log
file but not the key file." We want the value to be unreadable
without the per-user key file at `~/.dhc/secrets.key`.

v1.5.1.3 (audit hotfix): the `put_raw` and `delete` write paths
now self-heal the secrets directory via `_append_log` (which
calls `self._log.parent.mkdir(parents=True, exist_ok=True)`
before every write). This closes the latent
`FileNotFoundError` that fires when the secrets dir is removed
after server startup (e.g. a prior test cleanup or a manual
`rm` between sessions). See ADR-0108 v1.5.1.3 amendment.

Why a hand-rolled construction instead of the `cryptography` package?

The runtime is a sandboxed Python 3.14 that does not have network
access and is missing the optional `cryptography` dependency. The
available stdlib primitives are `hashlib` (HMAC, SHA-2, SHA-3, SHAKE)
and `hmac`. We build an authenticated stream cipher on top of those.

Envelope versions:

  v0x01  (HEADER="DHC1") — fixed scrypt salt b"dhc-secrets-v1".
  v0x02  (HEADER="DHC2") — per-envelope nonce as the scrypt salt.
  v0x03  (HEADER="DHC3") — extension blocks; strict 12-byte per-secret
          nonce (extension 0x0001) plus a 16-byte AEAD nonce, both
          random per secret. KDF salt is `strict_nonce (12) ||
          aead_nonce (16)` = 28 bytes — two independent random
          per-secret values. See `docs/adr/0012-envelope-v0x03.md`.
          Name binding (v1.3.3, ADR-0013). Read-only since v1.3.4.
  v0x04  (HEADER="DHC4") — same wire format as v0x03, with a
          canonical MAC input that length-prefixes the secret name
          with `u32 BE`. This closes the field-boundary shift
          forgery that the v0x03 `name ‖ ct` boundary admits. See
          `docs/adr/0014-canonical-mac-input.md`. Default since
          v1.3.4.

v0x01 and v0x02 share the same on-disk layout:

  HEADER (4) || nonce (16) || ciphertext (n) || tag (32)

v0x03 and v0x04 share the same on-disk layout:

  HEADER   (4)   b"DHC3" or b"DHC4"
  EXT_TOTAL (2)  uint16 BE, total extension bytes
  EXTS      (n)  length-prefixed extension blocks:
                  [ext_len:2 BE][ext_type:2 BE][ext_body:ext_len]
  NONCE    (16)  AEAD nonce (per envelope)
  CT        (m)  ciphertext
  TAG      (32)  auth tag

The v0x04 MAC input is canonical:
  mac_input = header || ext_area || nonce || len(name):u32 BE || name
  tag       = HMAC-SHA256(K_mac, mac_input || ct)

The v0x03 MAC input is non-canonical (legacy):
  mac_input = header || ext_area || nonce || name
  tag       = HMAC-SHA256(K_mac, mac_input || ct)

`open_envelope` dispatches on the header. New writes always produce
`DHC4`; old envelopes (`DHC1` / `DHC2` / `DHC3`) remain readable
indefinitely (DHC3 is read-only with its v0x03 MAC input rule).

Construction (encrypt-then-MAC, NIST SP 800-108 counter mode):

    K    = scrypt-derived 32-byte key from the key file
    For each write:
        nonce = urandom(16)
        ks_i  = HMAC-SHA256(K_ks, nonce || ctr_be32(i))   for i = 0,1,...
        ct    = plaintext XOR (ks_0 || ks_1 || ...)
        tag   = HMAC-SHA256(K_mac, header || nonce || ct)   separate MAC key

For v0x03 / v0x04 the KDF salt is `strict_nonce (12) || aead_nonce (16)`;
the MAC is over the wire bytes (`DHCx || ext_total || exts || nonce || ct`),
optionally followed by `len(name):u32 || name` for v0x04 (canonical) or
just `name` for v0x03 (legacy, name binding only, no length prefix).

v0x03 and v0x04 also bind the secret name into the MAC input
(v1.3.3+, ADR-0013). A valid v0x03 or v0x04 envelope stored under
a different name fails the tag check. v0x01 and v0x02 envelopes
do not bind the name; their tags pre-date this property.

Keystream construction is HMAC-SHA256 in counter mode (sometimes
called "HMAC-CTR"). The HMAC is keyed with a domain-separated
subkey `K_ks`; the counter `i` is a big-endian 32-bit integer
incremented per 32 bytes of output. This is a standard
construction — the underlying primitive is HMAC-SHA256, a PRF, so
the keystream is indistinguishable from random to any adversary
without the key. The authentication tag is computed with a separate
domain-separated subkey `K_mac` over `(header || nonce || ct)`.

Threat model:

The primary defense is the loopback bind. The encryption is defense
in depth: an attacker who can read `~/.dhc/secrets.log` but not
`~/.dhc/secrets.key` (mode 0o600) cannot decrypt values or forge
a tag. An attacker who has both files has full access; the
encryption is not designed to resist that.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets as _secrets
import struct
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


# ---------- Keystream: HMAC-SHA256 in counter mode ----------

_KEYSTREAM_BLOCK = 32  # HMAC-SHA256 output size

# v0x01 KDF salt (fixed, kept for backward-compatible reads).
_SALT_V1 = b"dhc-secrets-v1"


def _derive_keys(master_key: bytes, salt: bytes) -> tuple[bytes, bytes]:
    """Derive (keystream_key, mac_key) from the master key.

    `salt` is the scrypt KDF salt. v0x01 envelopes pass the fixed
    `b"dhc-secrets-v1"` salt; v0x02 envelopes pass the per-envelope
    nonce so each secret has a unique KDF input.

    The parameters (`n=2**10, r=8, p=1`) take ~50 ms on a modern CPU;
    secrets.put/get are not on the hot path.
    """
    if len(master_key) != 32:
        raise ValueError("master key must be 32 bytes")
    enc = hashlib.scrypt(master_key + b"-ks", salt=salt, n=2**10, r=8, p=1, dklen=32)
    mac = hashlib.scrypt(master_key + b"-mac", salt=salt, n=2**10, r=8, p=1, dklen=32)
    return enc, mac


def _keystream(ks_key: bytes, nonce: bytes, length: int) -> bytes:
    """Generate `length` bytes of HMAC-SHA256-counter-mode keystream.

    `nonce` is mixed into every block; the counter `i` is a 32-bit
    big-endian integer incremented per 32 bytes of output.
    """
    if length < 0:
        raise ValueError("length must be non-negative")
    out = bytearray()
    counter = 0
    while len(out) < length:
        block = hmac.new(
            ks_key,
            nonce + struct.pack(">I", counter),
            hashlib.sha256,
        ).digest()
        out.extend(block)
        counter += 1
    return bytes(out[:length])


# ---------- Envelope: encrypt-then-MAC ----------

NONCE_LEN = 16
TAG_LEN = 32
STRICT_NONCE_LEN = 12  # v0x03 extension 0x0001 body length

# v0x01 / v0x02 / v0x03 / v0x04 headers (4 ASCII bytes each).
HEADER_V1 = b"DHC1"
HEADER_V2 = b"DHC2"
HEADER_V3 = b"DHC3"
HEADER_V4 = b"DHC4"  # v1.3.4+ canonical MAC input (ADR-0014)

# Public alias: `HEADER` keeps the v1.2.x name; new code should use
# the versioned constant directly.
HEADER = HEADER_V1

# v0x03 extension types.
EXT_STRICT_NONCE = 0x0001

# v0x04 length-prefix width (4 bytes, big-endian u32) for the
# secret name in the canonical MAC input. See ADR-0014.
NAME_LEN_LEN = 4

# Sentinel: `extensions=None` means "auto-generate the v0x03 default
# extension set (just the strict nonce)".
_AUTO_EXTENSIONS = object()


def _pack_extension(ext_type: int, body: bytes) -> bytes:
    """Pack a single extension block: [ext_len:2 BE][ext_type:2 BE][body]."""
    if not 0 <= ext_type <= 0xFFFF:
        raise ValueError(f"ext_type out of range: {ext_type}")
    return struct.pack(">HH", len(body), ext_type) + body


def _serialize_extensions(extensions: list[tuple[int, bytes]]) -> bytes:
    """Serialize a list of `(ext_type, body)` tuples into the v0x03
    extension area. Returns `EXT_TOTAL (2) || ext0 || ext1 || ...`."""
    parts = bytearray()
    for ext_type, body in extensions:
        if not isinstance(body, (bytes, bytearray)):
            raise TypeError("extension body must be bytes")
        parts += _pack_extension(ext_type, bytes(body))
    total = len(parts)
    if total > 0xFFFF:
        raise ValueError(f"extension area too large: {total}")
    return struct.pack(">H", total) + bytes(parts)


def _parse_extensions(buf: bytes) -> dict[int, bytes]:
    """Parse a v0x03 extension area into a dict keyed by extension type.
    Unknown extension types are returned in the dict (the caller decides
    how to handle them); the parser itself does not reject them.
    """
    if len(buf) < 2:
        raise ValueError("extension area too short")
    total = struct.unpack(">H", buf[:2])[0]
    body = buf[2 : 2 + total]
    if 2 + total > len(buf):
        raise ValueError("extension area truncated")
    out: dict[int, bytes] = {}
    i = 0
    while i < len(body):
        if i + 4 > len(body):
            raise ValueError("truncated extension block")
        ext_len, ext_type = struct.unpack(">HH", body[i : i + 4])
        if i + 4 + ext_len > len(body):
            raise ValueError("truncated extension body")
        out[ext_type] = body[i + 4 : i + 4 + ext_len]
        i += 4 + ext_len
    return out


class SecretEnvelopeError(Exception):
    """Raised when an envelope cannot be decoded or fails the MAC check."""


def _seal_with(
    plaintext: bytes,
    master_key: bytes,
    header: bytes,
    *,
    extensions: list[tuple[int, bytes]] | object = _AUTO_EXTENSIONS,
    name: bytes = b"",
) -> bytes:
    """Internal: encrypt under a chosen header.

    The MAC is computed over `header || nonce || ct` (v0x01/v0x02) or
    `header || ext_area || nonce || ct` (v0x03), so the header and the
    extension area are bound to the tag.

    `extensions` is only meaningful when `header == HEADER_V3`. The
    sentinel `_AUTO_EXTENSIONS` (the default) means "auto-generate the
    standard v0x03 extension set" — a single extension, type
    `EXT_STRICT_NONCE`, with a fresh 12-byte body.

    `name` (v1.3.3+, ADR-0013) is the secret name. For v0x03 envelopes
    the name is appended to the MAC input so a valid envelope stored
    under a different name fails the tag check. For v0x01 / v0x02 the
    `name` argument is ignored (those envelopes pre-date name binding).
    """
    if not isinstance(plaintext, (bytes, bytearray)):
        raise TypeError("plaintext must be bytes")
    if not isinstance(master_key, (bytes, bytearray)) or len(master_key) != 32:
        raise ValueError("master_key must be 32 bytes")
    if not isinstance(name, (bytes, bytearray)):
        raise TypeError("name must be bytes")
    if header == HEADER_V1:
        salt = _SALT_V1
        ext_area = b""
        aead_nonce = _secrets.token_bytes(NONCE_LEN)
        mac_input = header + aead_nonce
    elif header == HEADER_V2:
        aead_nonce = _secrets.token_bytes(NONCE_LEN)
        salt = aead_nonce
        ext_area = b""
        mac_input = header + aead_nonce
    elif header == HEADER_V3:
        if extensions is _AUTO_EXTENSIONS or extensions is None:
            extensions = [(EXT_STRICT_NONCE, _secrets.token_bytes(STRICT_NONCE_LEN))]
        ext_area = _serialize_extensions(list(extensions))
        aead_nonce = _secrets.token_bytes(NONCE_LEN)
        # KDF salt is two independent random per-secret values.
        strict = next((b for t, b in extensions if t == EXT_STRICT_NONCE), b"")
        if len(strict) != STRICT_NONCE_LEN:
            raise ValueError(
                f"v0x03 requires an EXT_STRICT_NONCE extension "
                f"with {STRICT_NONCE_LEN}-byte body"
            )
        salt = strict + aead_nonce
        # v1.3.3+: the name is part of the MAC input (ADR-0013). The
        # name-binding property closes the confused-deputy attack where
        # a valid v0x03 envelope is stored under a different name.
        # v0x03 has the NON-CANONICAL MAC input: name ‖ ct are adjacent
        # variable-length fields (ADR-0014 Finding 1). v0x03 is now
        # read-only; new writes go to v0x04.
        mac_input = header + ext_area + aead_nonce + bytes(name)
    elif header == HEADER_V4:
        if extensions is _AUTO_EXTENSIONS or extensions is None:
            extensions = [(EXT_STRICT_NONCE, _secrets.token_bytes(STRICT_NONCE_LEN))]
        ext_area = _serialize_extensions(list(extensions))
        aead_nonce = _secrets.token_bytes(NONCE_LEN)
        # KDF salt is two independent random per-secret values (same
        # construction as v0x03).
        strict = next((b for t, b in extensions if t == EXT_STRICT_NONCE), b"")
        if len(strict) != STRICT_NONCE_LEN:
            raise ValueError(
                f"v0x04 requires an EXT_STRICT_NONCE extension "
                f"with {STRICT_NONCE_LEN}-byte body"
            )
        salt = strict + aead_nonce
        # v0x04 CANONICAL MAC input (ADR-0014): the name field is
        # length-prefixed with u32 BE so the boundary between name
        # and ct is unambiguous. Field-boundary shift forgeries on
        # the v0x03 name ‖ ct seam are impossible here.
        mac_input = (
            header
            + ext_area
            + aead_nonce
            + struct.pack(">I", len(bytes(name)))
            + bytes(name)
        )
    else:
        raise ValueError(f"unsupported envelope header: {header!r}")
    ks_key, mac_key = _derive_keys(bytes(master_key), salt)
    ks = _keystream(ks_key, aead_nonce, len(plaintext))
    ct = bytes(a ^ b for a, b in zip(plaintext, ks))
    tag = hmac.new(mac_key, mac_input + ct, hashlib.sha256).digest()
    return header + ext_area + aead_nonce + ct + tag


def seal(plaintext: bytes, master_key: bytes, name: bytes | str = b"") -> bytes:
    """Encrypt-then-MAC a plaintext under the master key.

    Since v1.3.4 the default is `DHC4` (canonical MAC input with
    `len(name):u32` prefix; ADR-0014). v1.3.2/v1.3.3 `DHC3` envelopes
    remain readable via `open_envelope`; v1.2.0/v1.3.0 `DHC1` and
    v1.3.1 `DHC2` envelopes are also readable.

    `name` (v1.3.3+, ADR-0013) is the secret name. v0x03 and v0x04
    envelopes (the defaults since v1.3.2 and v1.3.4 respectively)
    require a non-empty name: it is included in the MAC input so a
    valid envelope stored under a different name fails the tag check
    on read. v0x01 and v0x02 envelopes ignore the name. `name` may
    be a `str` (UTF-8 encoded internally) or `bytes`.
    """
    if not isinstance(master_key, (bytes, bytearray)) or len(master_key) != 32:
        raise ValueError("master_key must be 32 bytes")
    name_bytes = name.encode("utf-8") if isinstance(name, str) else bytes(name)
    if not name_bytes:
        # Allow empty name only for non-v0x03 / non-v0x04 (legacy)
        # headers via the low-level _seal_with; the public seal()
        # always writes v0x04 since v1.3.4, so a missing name is a
        # usage error.
        raise SecretEnvelopeError(
            "name is required for the v0x04 envelope (ADR-0013/0014); "
            "pass a non-empty str or bytes"
        )
    return _seal_with(plaintext, master_key, HEADER_V4, name=name_bytes)


def open_envelope(
    envelope: bytes,
    master_key: bytes,
    name: bytes | str = b"",
    require_binding: bool = True,
) -> bytes:
    """Open an envelope produced by `seal`. Verifies the tag in
    constant time and returns the plaintext. Raises
    `SecretEnvelopeError` on any failure.

    Dispatches on the 4-byte header:

    - `DHC1` (v0x01): legacy fixed-salt envelopes from v1.2.0/v1.3.0.
    - `DHC2` (v0x02): v1.3.1 envelopes with a per-envelope nonce
      as the scrypt salt.
    - `DHC3` (v0x03): v1.3.2/v1.3.3 envelopes with extension blocks.
      The KDF salt is `strict_nonce (12) || aead_nonce (16) = 28
      bytes`. The tag authenticates the secret name (ADR-0013) but
      uses a NON-CANONICAL MAC input (vulnerable to field-boundary
      shift forgeries; see ADR-0014). Read-only since v1.3.4.
    - `DHC4` (v0x04): v1.3.4+ envelopes with extension blocks AND
      a CANONICAL MAC input (`len(name):u32 BE` prefix; ADR-0014).
      Field-boundary shift forgeries are impossible.

    Pass `name=` (and let `require_binding=True`); pass
    `require_binding=False` only from migration scripts that
    read legacy v1.3.3 envelopes.

    Unknown headers raise `SecretEnvelopeError`.
    """
    if not isinstance(envelope, (bytes, bytearray)):
        raise TypeError("envelope must be bytes")
    if not isinstance(master_key, (bytes, bytearray)) or len(master_key) != 32:
        raise ValueError("master_key must be 32 bytes")
    if len(envelope) < len(HEADER_V1) + NONCE_LEN + TAG_LEN:
        raise SecretEnvelopeError("envelope too short")
    name_bytes = name.encode("utf-8") if isinstance(name, str) else bytes(name)
    header = bytes(envelope[: len(HEADER_V1)])
    if header == HEADER_V1:
        salt = _SALT_V1
        nonce = bytes(envelope[4 : 4 + NONCE_LEN])
        tag = envelope[-TAG_LEN:]
        ct = envelope[4 + NONCE_LEN : -TAG_LEN]
        mac_input = header + nonce
    elif header == HEADER_V2:
        nonce = bytes(envelope[4 : 4 + NONCE_LEN])
        salt = nonce
        tag = envelope[-TAG_LEN:]
        ct = envelope[4 + NONCE_LEN : -TAG_LEN]
        mac_input = header + nonce
    elif header == HEADER_V3:
        # v1.3.3+: v0x03 envelopes require a name to be supplied.
        if require_binding and not name_bytes:
            raise SecretEnvelopeError(
                "name binding required for v0x03 envelope (ADR-0013); "
                "pass name=... or call from SecretsService which "
                "knows the name"
            )
        # Parse extension area: 2-byte total + N extension blocks.
        if len(envelope) < 4 + 2 + NONCE_LEN + TAG_LEN:
            raise SecretEnvelopeError("envelope too short for v0x03")
        ext_total = struct.unpack(">H", envelope[4:6])[0]
        ext_area = envelope[4 : 6 + ext_total]
        if len(ext_area) != 2 + ext_total:
            raise SecretEnvelopeError("v0x03 extension area truncated")
        exts = _parse_extensions(ext_area)
        strict = exts.get(EXT_STRICT_NONCE)
        if strict is None or len(strict) != STRICT_NONCE_LEN:
            raise SecretEnvelopeError(
                f"v0x03 envelope missing EXT_STRICT_NONCE "
                f"({STRICT_NONCE_LEN} bytes)"
            )
        nonce = bytes(envelope[6 + ext_total : 6 + ext_total + NONCE_LEN])
        salt = strict + nonce
        body_start = 6 + ext_total + NONCE_LEN
        tag = envelope[-TAG_LEN:]
        ct = envelope[body_start:-TAG_LEN]
        # v0x03 NON-CANONICAL MAC input (legacy, ADR-0014). This is
        # the same MAC input rule v1.3.3 used, preserved here for
        # read-only compatibility. v0x03 envelopes are NOT immune to
        # field-boundary shift forgeries; the migration script is
        # the recommended way to upgrade them.
        mac_input = header + ext_area + nonce + name_bytes
    elif header == HEADER_V4:
        # v0x04: name is required (default; same as v0x03).
        if require_binding and not name_bytes:
            raise SecretEnvelopeError(
                "name binding required for v0x04 envelope (ADR-0013); "
                "pass name=... or call from SecretsService which "
                "knows the name"
            )
        # Parse extension area: 2-byte total + N extension blocks.
        if len(envelope) < 4 + 2 + NONCE_LEN + TAG_LEN:
            raise SecretEnvelopeError("envelope too short for v0x04")
        ext_total = struct.unpack(">H", envelope[4:6])[0]
        ext_area = envelope[4 : 6 + ext_total]
        if len(ext_area) != 2 + ext_total:
            raise SecretEnvelopeError("v0x04 extension area truncated")
        exts = _parse_extensions(ext_area)
        strict = exts.get(EXT_STRICT_NONCE)
        if strict is None or len(strict) != STRICT_NONCE_LEN:
            raise SecretEnvelopeError(
                f"v0x04 envelope missing EXT_STRICT_NONCE "
                f"({STRICT_NONCE_LEN} bytes)"
            )
        nonce = bytes(envelope[6 + ext_total : 6 + ext_total + NONCE_LEN])
        salt = strict + nonce
        body_start = 6 + ext_total + NONCE_LEN
        tag = envelope[-TAG_LEN:]
        ct = envelope[body_start:-TAG_LEN]
        # v0x04 CANONICAL MAC input (ADR-0014): the name is
        # length-prefixed with u32 BE. This disambiguates the
        # name ‖ ct boundary, closing the field-boundary shift
        # forgery that the v0x03 `name ‖ ct` seam admits.
        mac_input = (
            header
            + ext_area
            + nonce
            + struct.pack(">I", len(name_bytes))
            + name_bytes
        )
    else:
        raise SecretEnvelopeError(f"unknown envelope header: {header!r}")
    ks_key, mac_key = _derive_keys(bytes(master_key), salt)
    expected_tag = hmac.new(mac_key, mac_input + ct, hashlib.sha256).digest()
    if not hmac.compare_digest(expected_tag, tag):
        raise SecretEnvelopeError("authentication failed")
    ks = _keystream(ks_key, nonce, len(ct))
    return bytes(a ^ b for a, b in zip(ct, ks))


# ---------- Key file management ----------


DEFAULT_KEY_PATH = Path.home() / ".dhc" / "secrets.key"


def _ensure_dir(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(os, "chmod"):
        try:
            os.chmod(p.parent, 0o700)
        except OSError:
            pass


def load_or_create_master_key(key_path: Path = DEFAULT_KEY_PATH) -> bytes:
    """Load the 32-byte master key from `key_path`, creating a fresh
    one on first call. The key file is created with mode 0o600 on
    POSIX systems; on Windows the default ACL is sufficient given the
    loopback-bind threat model.
    """
    _ensure_dir(key_path)
    if key_path.exists():
        data = key_path.read_bytes()
        if len(data) != 32:
            raise SecretEnvelopeError(f"key file has wrong length: {len(data)}")
        return data
    key = _secrets.token_bytes(32)
    # Write atomically: tmp file then rename.
    tmp = key_path.with_suffix(".tmp")
    tmp.write_bytes(key)
    if hasattr(os, "chmod"):
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
    os.replace(tmp, key_path)
    if hasattr(os, "chmod"):
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
    return key


# ---------- Public SecretsService ----------


class SecretSourceType(str, Enum):
    """Closed enum of v1.6.0 secret source types.

    Bounded-entropy principle: new source types require a kernel
    ADR. The current set is:

    - `raw`: the value bytes are stored directly in the v0x04
      envelope. This is the v1.5.1.4 behavior.
    - `env`: the value is the name of an environment variable; the
      bytes are resolved at PUT time and stored in the envelope.
      The env var must be set in the harness process; missing env
      vars fail the PUT loudly. The disk artifact is the same
      v0x04 envelope, but the metadata records `source=env` and
      the env var name in `ref`.
    - `file`: the value is the path to a file; the bytes are read
      at PUT time. The file must be readable by the harness
      process. The disk artifact is the v0x04 envelope; the
      metadata records `source=file` and the file path in `ref`.
    """

    raw = "raw"
    env = "env"
    file = "file"


@dataclass(frozen=True)
class SecretSource:
    """Typed reference to a secret value.

    - For `raw`: `value` is the bytes (or str) to seal; `ref` is None.
    - For `env`: `value` is None; `ref` is the env var name.
    - For `file`: `value` is None; `ref` is the file path.

    `ref_hint` is what the Settings UI shows in the
    "configured · from env" / "configured · from file" pill.
    For `env` it's the env var name verbatim. For `file` it's the
    basename (e.g. `~/.ssh/openrouter.key`). For `raw` it's None
    (the v0x04 hint is computed from the value at read time).
    """

    type: SecretSourceType
    ref: str | None = None
    value: bytes | None = None

    def ref_hint(self) -> str | None:
        if self.type is SecretSourceType.env:
            return self.ref
        if self.type is SecretSourceType.file:
            return os.path.basename(self.ref) if self.ref else None
        return None

    def resolve(self) -> bytes:
        """Resolve the source to concrete bytes. Called at PUT time
        for `env` and `file`; raises `SecretSourceError` on failure.
        For `raw`, returns `value` directly.
        """
        if self.type is SecretSourceType.raw:
            if self.value is None:
                raise SecretSourceError("raw SecretSource has no value")
            return bytes(self.value)
        if self.type is SecretSourceType.env:
            if not self.ref:
                raise SecretSourceError("env SecretSource has no ref")
            v = os.environ.get(self.ref)
            if v is None:
                raise SecretSourceError(f"env var {self.ref!r} is not set")
            return v.encode("utf-8")
        if self.type is SecretSourceType.file:
            if not self.ref:
                raise SecretSourceError("file SecretSource has no ref")
            p = Path(self.ref)
            if not p.is_file():
                raise SecretSourceError(f"file {self.ref!r} is not a file")
            return p.read_bytes()
        raise SecretSourceError(f"unknown source type: {self.type!r}")


class SecretSourceError(RuntimeError):
    """Raised when a `SecretSource` cannot be resolved (missing
    env var, unreadable file, or invalid shape).
    """


def _hint_for(value: str) -> str:
    """Return a non-secret hint for a stored value: an ellipsis and
    the last 4 characters. Used by `SecretsService.list_metadata`
    to render an "OpenCode quadrant" re-display (research
    [[ADR-0108]]).

    v1.5.1.2 (audit hotfix): the previous shape was
    `first_3 + … + last_4` (e.g. `sk-…7890`). The 3-char prefix
    leaked the provider family (every major provider — OpenAI,
    Anthropic, OpenRouter — starts with `sk-`). The Settings
    modal's subtitle claims "Encrypted at rest, never sent to the
    browser in any response", which was misleading under the
    prefix-leak. The new shape is `…7890` (4 chars total, just
    the last 4). The `updated_at` field remains a separate signal
    for the user to confirm the right key was last touched.

    Returns an empty string when the value is shorter than 4
    characters (the hint would be the whole value).
    """
    if len(value) < 4:
        return ""
    return f"…{value[-4:]}"


class SecretsService:
    """In-process secret store backed by an append-only JSONL log
    on disk. Each entry is an envelope (bytes) written base64 to the
    log. Reads decrypt the latest envelope for a given name.

    The log file is `secrets_dir / "secrets.log"`. Reads scan from
    the start and apply operations in order, so a `del`
    is honored even though the log is append-only.
    """

    def __init__(self, secrets_dir: Path) -> None:
        self._dir = Path(secrets_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log = self._dir / "secrets.log"
        self._key = load_or_create_master_key(self._dir / "secrets.key")
        self._lock = threading.Lock()
        # In-memory cache: name -> plaintext (or None if deleted).
        # Populated lazily on first read so writes during a session
        # are reflected without a full rescan.
        self._cache: dict[str, bytes | None] = {}

    # ----- public API -----

    def put(self, name: str, value: str) -> None:
        """Encrypt and persist a secret. `value` is treated as utf-8."""
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(value, str):
            raise ValueError("value must be a string")
        self.put_raw(name, value.encode("utf-8"))

    def put_raw(self, name: str, value: bytes) -> None:
        """Encrypt and persist a raw `bytes` value. Used by stores
        that need to round-trip binary data (e.g. JSON-encoded
        `ModelConfig` blobs in ADR-0011).

        v1.6.0: this is a thin wrapper around `put_source` with a
        raw `SecretSource`. The on-disk record is identical to the
        v1.5.1.4 shape (`{"op":"set","name":...,"blob":...}`); no
        migration is required for existing logs.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("value must be bytes")
        self.put_source(name, SecretSource(type=SecretSourceType.raw, ref=None, value=bytes(value)))

    def put_source(self, name: str, source: SecretSource) -> None:
        """Encrypt and persist a secret from a typed source.

        v1.6.0: polymorphic JSONL schema. For `raw` sources, the
        record is `{"op":"set","name":...,"blob":<base64 envelope>}`
        (the v1.5.1.4 shape, unchanged). For `env` and `file`
        sources, the value bytes are resolved at PUT time and the
        record is `{"op":"set","name":...,"source":"env|file",
        "ref":<ref>,"blob":<base64 envelope>,"hint":<ref_hint>}`.
        The envelope is always present regardless of source type
        (the source is the *origin* of the bytes, not a substitute
        for sealing at rest).

        Missing env vars and unreadable files fail the PUT
        loudly. There is no fallback to `raw` mode.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(source, SecretSource):
            raise TypeError("source must be a SecretSource")
        value = source.resolve()
        envelope = seal(value, self._key, name=name)
        if source.type is SecretSourceType.raw:
            record = {
                "op": "set",
                "name": name,
                "blob": base64.b64encode(envelope).decode("ascii"),
            }
        else:
            # v1.6.0 polymorphic shape: source + ref are recorded
            # alongside the envelope so the metadata endpoint can
            # show the user *where* the secret came from without
            # decrypting. The envelope is still present (defense
            # in depth: even if source=env is recorded, the bytes
            # at rest are sealed, not the env var name).
            record = {
                "op": "set",
                "name": name,
                "source": source.type.value,
                "ref": source.ref,
                "ref_hint": source.ref_hint(),
                "blob": base64.b64encode(envelope).decode("ascii"),
            }
        with self._lock:
            self._append_log(record)
            self._cache[name] = value

    def get_source(self, name: str) -> SecretSource | None:
        """Return the structured source for `name`, or `None` if
        missing/tombstoned. Reads the JSONL log; for `env`/`file`
        sources, the `SecretSource` carries the env var name or
        file path. For `raw` sources, the `value` field of the
        returned `SecretSource` is the decrypted bytes (this is
        the only way to round-trip the raw value through the
        structured API; the `value` field is None for `env`/`file`).
        """
        with self._lock:
            self._replay()
            if name not in self._cache:
                return None
            if self._cache[name] is None:
                return None
            # Find the source info from the latest set record.
            rec = self._last_set_record(name)
            if rec is None or "source" not in rec:
                # Legacy v1.5.1.4 record: treat as raw.
                return SecretSource(
                    type=SecretSourceType.raw,
                    ref=None,
                    value=self._cache[name],
                )
            st = SecretSourceType(rec["source"])
            return SecretSource(
                type=st,
                ref=rec.get("ref"),
                value=self._cache[name] if st is SecretSourceType.raw else None,
            )

    # v1.5.1.3 (audit hotfix): self-heal the secrets dir + log file
    # before every write. The constructor's `mkdir` only runs at
    # startup; if the dir is removed after startup (a prior test
    # cleanup, a manual `rm`, a migration that touched the dir),
    # the next `open("a")` raises `FileNotFoundError`. Re-creating
    # the parent on every write makes the path robust to any prior
    # state. The lock is the same `self._lock` already held by
    # `put_raw` / `delete`, so concurrent writes still serialize.
    def _append_log(self, record: dict[str, object]) -> None:
        self._log.parent.mkdir(parents=True, exist_ok=True)
        with self._log.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, separators=(",", ":")) + "\n")

    def seal_raw(self, name: str, value: bytes) -> bytes:
        """Encrypt a value with the master key, return the envelope.

        v1.5.0 (ADR-0020): the attachment service uses this to seal
        out-of-line attachment bytes. The envelope is *not* persisted
        to the JSONL log; the caller stores the bytes on disk and
        reads them back via `open_raw`. The name-binding property
        (ADR-0013) carries over: the envelope's MAC includes the
        name, so a ref from one session cannot be opened in another.
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(value, (bytes, bytearray)):
            raise TypeError("value must be bytes")
        return seal(bytes(value), self._key, name=name)

    def open_raw(self, name: str, envelope: bytes) -> bytes:
        """Decrypt an envelope produced by `seal_raw`.

        Returns the original plaintext. Raises `SecretEnvelopeError`
        if the tag does not verify (e.g. the envelope is from a
        different session, was tampered with, or the master key has
        rotated).
        """
        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        if not isinstance(envelope, (bytes, bytearray)):
            raise TypeError("envelope must be bytes")
        return open_envelope(bytes(envelope), self._key, name=name)

    def get(self, name: str) -> str | None:
        raw = self.get_raw(name)
        return raw.decode("utf-8") if raw is not None else None

    def get_raw(self, name: str) -> bytes | None:
        """Decrypt and return the raw `bytes` value for `name`,
        or `None` if the name is missing or tombstoned.

        Used by stores that need binary round-trip (ADR-0011
        `ModelConfigStore`).
        """
        with self._lock:
            if name in self._cache:
                cached = self._cache[name]
                return cached
            self._replay()
            cached = self._cache.get(name)
            return cached

    def delete(self, name: str) -> bool:
        with self._lock:
            self._replay()
            # Only emit a tombstone if the name currently resolves to
            # a live (non-deleted) value. Deleting a missing key (or
            # one that is already tombstoned) is a no-op and returns
            # False, so the log stays compact and idempotent.
            current = self._cache.get(name)
            if current is None:
                return False
            record = {"op": "del", "name": name}
            self._append_log(record)
            self._cache[name] = None
            return True

    def list(self) -> list[str]:
        with self._lock:
            self._replay()
            return sorted(n for n, v in self._cache.items() if v is not None)

    def list_metadata(self) -> list[dict[str, object]]:
        """Return a metadata-only list of stored secrets. The
        shape is `[{name, configured, updated_at, hint}]`.

        v1.5.1.2 (audit hotfix): `hint` is now `…+last_4` only
        (e.g. `…7890`), no first-3 prefix. The previous
        `first_3+…+last_4` shape (e.g. `sk-…7890`) leaked the
        provider family (every major provider starts with
        `sk-`). The hint is still not a secret — at 5 visible
        characters, it is not enough to reconstruct the value —
        but it now satisfies the modal's "never sent to the
        browser in any response" claim.

        `updated_at` is the file mtime of `secrets.log` at the
        time of the call (best-effort; the log is append-only so
        this is the timestamp of the latest write).
        """
        with self._lock:
            self._replay()
            try:
                updated_at = self._log.stat().st_mtime
            except OSError:
                updated_at = 0.0
            out: list[dict[str, object]] = []
            for name, v in sorted(self._cache.items()):
                if v is None:
                    continue
                try:
                    s = v.decode("utf-8")
                except UnicodeDecodeError:
                    s = ""
                hint = _hint_for(s)
                out.append({
                    "name": name,
                    "configured": True,
                    "updated_at": updated_at,
                    "hint": hint,
                })
            return out

    def check_permissions(self) -> None:
        """Verify the secrets log is owner-readable/writable only
        (mode 0600). Raises `PermissionError` if the file exists
        with a permissive mode, in keeping with the GitHub-shaped
        contract (ADR-0108): fail loud, never silently fall back.
        """
        if not self._log.exists():
            return
        try:
            st = self._log.stat()
        except OSError as exc:
            raise PermissionError(f"cannot stat secrets log: {exc}") from exc
        # Mask out file-type bits; we only care about the perm bits.
        mode = st.st_mode & 0o777
        if mode & 0o077:
            self._insecure_permissions = True
            raise PermissionError(
                f"secrets log {self._log} is mode 0o{mode:o}; "
                "expected 0o600 (owner-only). Run "
                f"'chmod 600 {self._log}' and restart."
            )

    @property
    def is_insecure_permissions(self) -> bool:
        """True if the secrets log was observed with permissive
        permissions at startup. The C1 service uses this to
        reject writes (PUT/DELETE) with 403 until the user
        fixes the mode.
        """
        return getattr(self, "_insecure_permissions", False)

    # ----- internals -----

    def _replay(self) -> None:
        self._cache = {}
        self._last_records = {}
        if not self._log.exists():
            return
        for line in self._log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            op = rec.get("op")
            name = rec.get("name")
            if op == "set":
                # v1.6.0: polymorphic schema. `raw` records look like
                # v1.5.1.4 (`{"op":"set","name":...,"blob":...}`).
                # `env`/`file` records add `source` and `ref` and
                # still include `blob` (defense in depth: the
                # envelope is always sealed at rest). The reader
                # does not branch on the source field; it only
                # needs the blob.
                blob = base64.b64decode(rec["blob"])
                pt = open_envelope(blob, self._key, name=name)
                self._cache[name] = pt
                self._last_records[name] = rec
            elif op == "del":
                self._cache[name] = None
                self._last_records[name] = rec
            else:
                # Unknown op: ignore but do not fail; the log is
                # forward-compatible.
                pass

    def _last_set_record(self, name: str) -> dict | None:
        """Return the most recent record for `name` (any op). Used
        by `get_source` to recover the structured `source`/`ref`
        for non-raw records. Must be called under `self._lock`.
        """
        if not hasattr(self, "_last_records"):
            self._replay()
        return self._last_records.get(name)


__all__ = [
    "DEFAULT_KEY_PATH",
    "NONCE_LEN",
    "TAG_LEN",
    "STRICT_NONCE_LEN",
    "NAME_LEN_LEN",
    "HEADER",
    "HEADER_V1",
    "HEADER_V2",
    "HEADER_V3",
    "HEADER_V4",
    "EXT_STRICT_NONCE",
    "SecretEnvelopeError",
    "SecretSource",
    "SecretSourceError",
    "SecretSourceType",
    "SecretsService",
    "load_or_create_master_key",
    "open_envelope",
    "seal",
]
