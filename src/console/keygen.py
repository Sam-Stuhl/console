"""Generate a Fernet key file: python -m console.keygen <path>"""

import sys
from pathlib import Path

from cryptography.fernet import Fernet


def main(path_str: str) -> None:
    path = Path(path_str)
    if path.exists():
        sys.exit(f"refusing to overwrite existing {path}")
    path.write_bytes(Fernet.generate_key() + b"\n")
    path.chmod(0o600)
    print(f"wrote new console key to {path}")
    print("back this file up; losing it means losing every stored secret")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python -m console.keygen <path>")
    main(sys.argv[1])
