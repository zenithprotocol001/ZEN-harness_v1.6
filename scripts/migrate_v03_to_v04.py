"""One-time migration from v0x03 to v0x04 envelopes (ADR-0014).

v1.3.4 introduces the v0x04 envelope (`DHC4`) with a canonical
MAC input that length-prefixes the secret name with `u32 BE`.
This closes the field-boundary shift forgery that the v0x03
`name ‖ ct` boundary admits. v0x03 envelopes remain readable
(read-only with their non-canonical MAC input rule), but new
writes go to v0x04.

This script migrates an existing `secrets.log` from v0x03 to
v0x04 by:

1. Reading each entry in `<secrets_dir>/secrets.log` (JSONL).
2. For each `set` entry, opening the envelope with
   `open_envelope(blob, key, name=name, require_binding=False)`.
3. **Refusing to migrate envelopes where `name == b""`**
   (anonymous v0x03 envelopes are a downgrade vector; see
   ADR-0013 §"Primitives path" and ADR-0014 §"Migration").
4. Re-sealing via `seal(value, key, name=name)`. The seal
   writes v0x04.
5. Writing a new `<secrets_dir>/secrets.log` atomically
   (tmp + rename). The old log is preserved at
   `<secrets_dir>/secrets.log.v03.bak`.
6. Idempotent: running twice is a no-op (no v0x03 entries
   remain after the first run).

Usage:
    python -m scripts.migrate_v03_to_v04 <secrets_dir>

The script is opt-in. Users with no v0x03 envelopes do not
need to run it.
"""
from __future__ import annotations

import base64
import json
import shutil
import sys
import tempfile
from pathlib import Path

from dhc.cordis.secrets import (
    HEADER_V3,
    HEADER_V4,
    SecretEnvelopeError,
    SecretsService,
    open_envelope,
    seal,
)


def migrate(secrets_dir: Path) -> int:
    """Migrate a secrets log from v0x03 to v0x04 envelopes.

    Returns the number of entries migrated. Raises
    `SecretEnvelopeError` if an anonymous v0x03 envelope
    (sealed with `name=b""`) is encountered, or if any v0x03
    envelope cannot be decrypted.
    """
    secrets_dir = Path(secrets_dir)
    log_path = secrets_dir / "secrets.log"
    if not log_path.exists():
        print(f"no secrets.log at {log_path}; nothing to migrate")
        return 0
    # Load the master key via SecretsService (re-uses its
    # load_or_create_master_key path so the key file location
    # matches the runtime).
    service = SecretsService(secrets_dir)
    key = service._key  # type: ignore[attr-defined]
    # Read all entries.
    entries: list[dict] = []
    migrated = 0
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            op = rec.get("op")
            if op == "set":
                blob = base64.b64decode(rec["blob"])
                name = rec.get("name", "")
                # Refuse anonymous v0x03 envelopes (downgrade vector).
                if blob[:4] == HEADER_V3 and not name:
                    raise SecretEnvelopeError(
                        f"refusing to migrate anonymous v0x03 envelope: "
                        f"name={name!r} is empty; this is a downgrade vector"
                    )
                if blob[:4] == HEADER_V3:
                    # Read with require_binding=False (v0x03 legacy path).
                    pt = open_envelope(blob, key, name=name, require_binding=False)
                    # Re-seal as v0x04. The seal always writes v0x04.
                    new_blob = seal(pt, key, name=name)
                    rec["blob"] = base64.b64encode(new_blob).decode("ascii")
                    migrated += 1
            entries.append(rec)
    # Write the new log atomically. Preserve the old log at
    # <log>.v03.bak for safety.
    backup_path = log_path.with_suffix(".log.v03.bak")
    if not backup_path.exists():
        shutil.copy2(log_path, backup_path)
    # Atomic write via tmp + rename.
    fd, tmp_path = tempfile.mkstemp(
        dir=str(secrets_dir), prefix=".secrets.log.migrate.", suffix=".tmp"
    )
    try:
        with open(fd, "w", encoding="utf-8") as f:
            for rec in entries:
                f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        Path(tmp_path).replace(log_path)
    except Exception:
        Path(tmp_path).unlink(missing_ok=True)
        raise
    print(f"migrated {migrated} v0x03 entries to v0x04 in {log_path}")
    print(f"old log preserved at {backup_path}")
    return migrated


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m scripts.migrate_v03_to_v04 <secrets_dir>")
        return 2
    secrets_dir = Path(argv[1])
    if not secrets_dir.is_dir():
        print(f"error: {secrets_dir} is not a directory")
        return 2
    try:
        migrate(secrets_dir)
    except SecretEnvelopeError as e:
        print(f"migration failed: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
