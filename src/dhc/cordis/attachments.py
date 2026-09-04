"""dhc.cordis.attachments: attachment storage on the v0x04 envelope
(ADR-0020, ADR-0021).

Two storage classes:

- **Inline** (text-like MIME): the body is a UTF-8 string stored
  directly in `Message.attachments: list[InlineAttachment]`. The
  audit log projects the body through the 62-gate
  (`audit-text` domain per ADR-0021). Lossy on invalid UTF-8
  sequences; the caller-provided bytes are accepted.

- **Out-of-line** (binary MIME): the bytes are stored on disk
  under `~/.dhc/attachments/{session_id}/{uuid}.bin` and wrapped
  in a v0x04 envelope with name `attachment:{session_id}:{uuid}`.
  The message carries only the ref + metadata (`asset` domain;
  no 62-filter applied; bytes round-trip exactly).

Routing by MIME class (not by byte size) per the v1.5.0 design
decision: text-like content is small and structured, binary
content is bulk and opaque. The `MIME_INLINE` and `MIME_OUT_OF_LINE`
frozensets are the source of truth.

Size limits (ADR-0020):

- 10 MB per file (per-attachment).
- 25 MB per message (sum of all attachments on a single message).

Name binding (ADR-0013 + ADR-0014): the v0x04 envelope name is
`attachment:{session_id}:{uuid}`. The session_id is part of the
name so cross-session ref reuse fails the MAC. The `attachment:`
prefix keeps the namespace invisible to `key_lookup`'s
`llm_provider_` filter.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import threading
from pathlib import Path
from typing import Any


MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB per file
MAX_MESSAGE_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 MB per message

# MIME routing (ADR-0020): inline for text/SVG, out-of-line for binary.
MIME_INLINE: frozenset[str] = frozenset({
    "text/plain",
    "text/markdown",
    "image/svg+xml",
})
MIME_OUT_OF_LINE: frozenset[str] = frozenset({
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "audio/mpeg",
    "audio/wav",
    "application/pdf",
})
MIME_ALLOWLIST: frozenset[str] = MIME_INLINE | MIME_OUT_OF_LINE

ATTACHMENT_REF_RE = re.compile(
    r"^attachment:(?P<session_id>[A-Za-z0-9_-]+):(?P<uuid>[0-9a-f]{32})$"
)

_ID_ALPHABET = "0123456789abcdef"


def _new_uuid() -> str:
    return "".join(secrets.choice(_ID_ALPHABET) for _ in range(32))


def parse_attachment_ref(ref: str) -> tuple[str, str] | None:
    """Parse `attachment:{session_id}:{uuid}` into `(session_id, uuid)`.

    Returns `None` if the ref does not match the canonical format.
    Used by the route handlers to extract the path components.
    """
    if not isinstance(ref, str):
        return None
    m = ATTACHMENT_REF_RE.match(ref)
    if not m:
        return None
    return m.group("session_id"), m.group("uuid")


def attachment_ref(session_id: str, uuid: str) -> str:
    """Build the canonical ref string."""
    return f"attachment:{session_id}:{uuid}"


def is_mime_allowed(mime: str) -> bool:
    return mime in MIME_ALLOWLIST


def is_mime_inline(mime: str) -> bool:
    return mime in MIME_INLINE


class AttachmentError(Exception):
    """Base error for the attachment service."""


class AttachmentMIMEError(AttachmentError):
    """The MIME type is not on the allowlist."""


class AttachmentSizeError(AttachmentError):
    """The attachment exceeds a size limit (per-file or per-message)."""


class AttachmentNotFoundError(AttachmentError):
    """The ref does not exist in the attachment store."""


class AttachmentService:
    """Out-of-line attachment storage on the v0x04 envelope.

    The service is the only writer/reader of the `~/.dhc/attachments/`
    directory tree. In-memory text/SVG attachments are not stored by
    the service at all — they live in `Message.attachments:
    list[InlineAttachment]` and the service has no opinion on them.
    """

    def __init__(
        self,
        attachments_dir: Path,
        secrets_service: Any,  # SecretsService from dhc.cordis.secrets
    ) -> None:
        self._root = Path(attachments_dir)
        self._root.mkdir(parents=True, exist_ok=True)
        self._secrets = secrets_service
        self._lock = threading.RLock()

    # ----- out-of-line put/get/delete -----

    def put(
        self,
        session_id: str,
        payload: bytes,
        mime: str,
    ) -> dict[str, Any]:
        """Store an out-of-line attachment.

        Returns the ref metadata:
            `{"ref": "attachment:{sid}:{uuid}", "mime": ..., "sha256": ..., "size": N}`

        Raises:
            AttachmentMIMEError: if the MIME is not in `MIME_OUT_OF_LINE`.
                (Text/SVG content goes inline; do not call this with
                a text-like MIME.)
            AttachmentSizeError: if `len(payload) > MAX_ATTACHMENT_BYTES`.

        The payload is sealed in a v0x04 envelope with name
        `attachment:{session_id}:{uuid}`. The seal is name-bound
        (ADR-0013), so a ref from one session cannot be opened in
        another.
        """
        if mime not in MIME_OUT_OF_LINE:
            raise AttachmentMIMEError(
                f"mime {mime!r} is not out-of-line; "
                f"text-like MIME goes inline (MIME_INLINE = {sorted(MIME_INLINE)})"
            )
        if len(payload) > MAX_ATTACHMENT_BYTES:
            raise AttachmentSizeError(
                f"attachment size {len(payload)} exceeds per-file limit "
                f"{MAX_ATTACHMENT_BYTES} bytes"
            )
        uuid = _new_uuid()
        ref = attachment_ref(session_id, uuid)
        with self._lock:
            sess_dir = self._root / session_id
            sess_dir.mkdir(parents=True, exist_ok=True)
            # Seal in a v0x04 envelope and store the envelope bytes.
            envelope = self._secrets.seal_raw(ref, payload)
            (sess_dir / f"{uuid}.bin").write_bytes(envelope)
            # Metadata sidecar.
            sha = hashlib.sha256(payload).hexdigest()
            meta = {
                "ref": ref,
                "mime": mime,
                "sha256": sha,
                "size": len(payload),
                "created_at_ms": int(__import__("time").time() * 1000),
            }
            (sess_dir / f"{uuid}.json").write_text(
                __import__("json").dumps(meta, sort_keys=True),
                encoding="utf-8",
            )
        return meta

    def get(self, ref: str) -> tuple[bytes, dict[str, Any]]:
        """Fetch an out-of-line attachment by ref.

        Returns `(payload_bytes, metadata_dict)`. The payload is
        the original bytes, opened from the v0x04 envelope and
        verified by the MAC. A tampered envelope raises
        `SecretEnvelopeError`.

        Raises:
            AttachmentNotFoundError: if the ref is not in the store.
            ValueError: if the ref format is invalid.
        """
        parsed = parse_attachment_ref(ref)
        if parsed is None:
            raise ValueError(f"invalid attachment ref: {ref!r}")
        session_id, uuid = parsed
        with self._lock:
            meta_path = self._root / session_id / f"{uuid}.json"
            bin_path = self._root / session_id / f"{uuid}.bin"
            if not meta_path.exists() or not bin_path.exists():
                raise AttachmentNotFoundError(f"attachment {ref!r} not found")
            import json as _json
            meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            envelope = bin_path.read_bytes()
        # Open the envelope. `open_raw` returns the original
        # plaintext; the MAC is verified.
        payload = self._secrets.open_raw(ref, envelope)
        return payload, meta

    def delete(self, ref: str) -> None:
        """Remove an out-of-line attachment.

        Raises:
            AttachmentNotFoundError: if the ref is not in the store.
            ValueError: if the ref format is invalid.
        """
        parsed = parse_attachment_ref(ref)
        if parsed is None:
            raise ValueError(f"invalid attachment ref: {ref!r}")
        session_id, uuid = parsed
        with self._lock:
            meta_path = self._root / session_id / f"{uuid}.json"
            bin_path = self._root / session_id / f"{uuid}.bin"
            if not meta_path.exists() or not bin_path.exists():
                raise AttachmentNotFoundError(f"attachment {ref!r} not found")
            meta_path.unlink()
            bin_path.unlink()
            # Try to remove the now-empty session dir; ignore
            # if other attachments still live there.
            try:
                (self._root / session_id).rmdir()
            except OSError:
                pass

    # ----- per-message size accounting -----

    @staticmethod
    def message_attachment_size(
        inline: list[dict[str, Any]] | None = None,
        out_of_line: list[dict[str, Any]] | None = None,
        inline_bodies: list[bytes | str] | None = None,
    ) -> int:
        """Compute the total attachment bytes for a single message.

        Sum: `len(inline_body) + out_of_line.size` for each
        attachment. The caller provides the inline bodies
        separately because the message schema stores them as
        strings (the original bytes are the UTF-8 encoding of the
        string).

        Raises:
            AttachmentSizeError: if the sum exceeds
                `MAX_MESSAGE_ATTACHMENT_BYTES`.
        """
        total = 0
        if inline and inline_bodies:
            for body in inline_bodies:
                if isinstance(body, str):
                    total += len(body.encode("utf-8"))
                else:
                    total += len(body)
        if out_of_line:
            for ref in out_of_line:
                total += int(ref.get("size", 0))
        if total > MAX_MESSAGE_ATTACHMENT_BYTES:
            raise AttachmentSizeError(
                f"message attachment total {total} exceeds per-message "
                f"limit {MAX_MESSAGE_ATTACHMENT_BYTES} bytes"
            )
        return total


__all__ = [
    "AttachmentError",
    "AttachmentMIMEError",
    "AttachmentNotFoundError",
    "AttachmentService",
    "AttachmentSizeError",
    "MAX_ATTACHMENT_BYTES",
    "MAX_MESSAGE_ATTACHMENT_BYTES",
    "MIME_ALLOWLIST",
    "MIME_INLINE",
    "MIME_OUT_OF_LINE",
    "attachment_ref",
    "is_mime_allowed",
    "is_mime_inline",
    "parse_attachment_ref",
]
