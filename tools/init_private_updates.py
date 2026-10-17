"""Prepare a private update store and owner authorization without printing it."""
import argparse
import hashlib
import os
import secrets
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("private-updates"))
    args = parser.parse_args()
    directory = args.directory.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    token_file, config = directory / "client-token.txt", directory / "updates.env"
    if token_file.exists() or config.exists():
        raise SystemExit("Existing authorization or configuration found; refusing to overwrite.")
    token = secrets.token_urlsafe(48)
    fd = os.open(token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as file:
        file.write(token + "\n")
    (directory / "releases").mkdir(exist_ok=True)
    digest = hashlib.sha256(token.encode()).hexdigest()
    store = (directory / "releases").as_posix()
    config.write_text(f'PAPERPLANE_UPDATE_TOKEN_SHA256={digest}\nPAPERPLANE_UPDATE_STORE="{store}"\nPAPERPLANE_UPDATE_PORT=8810\n', encoding="utf-8")
    config.chmod(0o600)
    print(f"Compose configuration: {config}\nClient authorization file (private): {token_file}\nNo authorization value was printed.")


if __name__ == "__main__":
    main()
