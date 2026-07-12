"""Restore a backup bundle produced by console.backup.engine.

    python -m console.backup.restore <bundle-file> [--out DIR]

Decrypts with the mounted passphrase (CONSOLE_BACKUP_PASSPHRASE_FILE) and
extracts console.db and console_key into --out (default ./restored). It never
touches a running console's state; putting the files into place is a deliberate
manual step, printed at the end."""

import argparse
import io
import sys
import tarfile
from pathlib import Path

from console.backup import crypto


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m console.backup.restore")
    parser.add_argument("bundle", help="path to a console-backup-*.bin file")
    parser.add_argument("--out", default="restored", help="output directory")
    args = parser.parse_args(argv)

    bundle = Path(args.bundle).read_bytes()
    try:
        tar_bytes = crypto.decrypt(bundle)
    except (crypto.BackupRestoreError, crypto.BackupPassphraseNotConfigured) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:gz") as tar:
        for member in ("console.db", "console_key"):
            extracted = tar.extractfile(member)
            if extracted is None:
                print(f"error: {member} missing from bundle", file=sys.stderr)
                return 1
            (out / member).write_bytes(extracted.read())

    print(f"restored console.db and console_key to {out}/")
    print("to use them: stop the console, put console.db at CONSOLE_DB_PATH and")
    print("console_key at CONSOLE_KEY_FILE, then start it again.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
