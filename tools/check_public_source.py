"""Check public source paths and text without printing private matched values.

Default: tracked working-tree files. --all-history: every reachable tree/blob.
--directory: all files in an exported directory, without requiring a Git repo.
This complements Gitleaks; it does not recognize every kind of personal data.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Iterator


WINDOWS_HOME = re.compile(r"(?i)\b[a-z]:[\\/]+Users[\\/]+([A-Za-z0-9_.@ -]+)")
UNIX_HOME = re.compile(r"/(?:home|Users)/([A-Za-z0-9_.@-]+)")
ROOT_HOME = re.compile(r"/root/(?:[A-Za-z0-9_.-]+)")
OWNER_DOMAIN = re.compile(r"(?i)\b(?:[a-z0-9-]+\.)*" + "aiclawonline" + r"\.website\b")
# Explicit documentation examples, the container account and hosted CI accounts.
GENERIC_USERS = {"user", "username", "example", "public", "default", "all users",
                 "archive", "runner", "runneradmin", "runner~1"}
PRIVATE_DIRS = {"data", "backups", "private-updates", "browser", "browser-profile",
                "browser-profiles", "browser_profile", "browser_profiles", "browser-data",
                ".auth", ".ssh", ".lark-cli"}
PRIVATE_FILES = {"private.json", "cookies", "cookies.json", "cookies.txt",
                 "storage-state.json", "storage_state.json", "storagestate.json",
                 "browser-state.json", "browser_state.json", "platform-accounts.json",
                 ".paperplane-paths.json", ".gitleaksignore", "login data", "web data"}


@dataclass(frozen=True)
class Finding:
    path: str
    line: int
    kind: str
    revision: str = ""

    def render(self) -> str:
        # JSON quoting prevents filenames from injecting terminal control sequences.
        location = json.dumps(self.path, ensure_ascii=True)
        version = f" [{self.revision}]" if self.revision else ""
        return f"{location}:{self.line}: {self.kind}{version}"


def forbidden_path(path: str) -> bool:
    parts = [part.lower() for part in PurePosixPath(path).parts]
    name = parts[-1]
    if any(part in PRIVATE_DIRS or part.startswith("data-") for part in parts[:-1]):
        return True
    if name in PRIVATE_FILES or name.startswith("private.json."):
        return True
    is_env = name == ".env" or name.startswith(".env.") or name.endswith(".env")
    if is_env and not (name.startswith(".env") and name.endswith(".example")):
        return True
    return bool(re.search(r"\.(?:db|sqlite\d*)(?:-(?:wal|shm|journal))?$", name))


def check_content(path: str, data: bytes, revision: str = "") -> list[Finding]:
    findings = []
    if forbidden_path(path):
        findings.append(Finding(path, 1, "private-runtime-file", revision))
    if data.startswith(b"SQLite format 3\0"):
        findings.append(Finding(path, 1, "sqlite-database-content", revision))
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = data.decode("utf-16", errors="replace")
    else:
        text = data.decode("utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        kinds = set()
        if OWNER_DOMAIN.search(line):
            kinds.add("owner-service-domain")
        for rule, kind in ((WINDOWS_HOME, "personal-windows-home"),
                           (UNIX_HOME, "personal-unix-home")):
            if any(match.group(1).strip().lower() not in GENERIC_USERS
                   for match in rule.finditer(line)):
                kinds.add(kind)
        if ROOT_HOME.search(line):
            kinds.add("personal-root-home")
        findings.extend(Finding(path, number, kind, revision) for kind in sorted(kinds))
    return findings


def git(root: Path, *args: str) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def working_files(root: Path, directory: bool) -> Iterator[tuple[str, bytes, str]]:
    if directory:
        paths = sorted(path for path in root.rglob("*") if ".git" not in path.relative_to(root).parts
                       and (path.is_file() or path.is_symlink()))
    else:
        paths = [root / name.decode("utf-8", errors="surrogateescape")
                 for name in git(root, "ls-files", "-z").split(b"\0") if name]
    for path in paths:
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            # Never follow a tracked link into a private directory outside the source.
            raise ValueError(f"unsupported symlink at {json.dumps(relative, ensure_ascii=True)}")
        # Deleted tracked files do not become part of a source export.
        if path.is_file():
            yield relative, path.read_bytes(), ""


def historical_files(root: Path) -> Iterator[tuple[str, bytes, str]]:
    if git(root, "rev-parse", "--is-shallow-repository").strip() == b"true":
        raise ValueError("history scan requires a complete clone (fetch-depth: 0)")
    seen = set()
    # Enumerate every tree, retaining renames and identical content at different paths.
    # rev-list --objects alone reports only one path for a reused blob.
    for revision in git(root, "rev-list", "--all").decode("ascii").splitlines():
        for entry in git(root, "ls-tree", "-rz", "--full-tree", revision).split(b"\0"):
            if not entry:
                continue
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, oid = metadata.split()
            path = raw_path.decode("utf-8", errors="surrogateescape")
            if kind != b"blob":
                raise ValueError("history contains an unscanned submodule")
            if mode == b"120000":
                raise ValueError(f"history contains a symlink at {json.dumps(path, ensure_ascii=True)}")
            key = (path, oid)
            if key in seen:
                continue
            seen.add(key)
            yield path, git(root, "cat-file", "blob", oid.decode("ascii")), revision[:12]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--all-history", action="store_true")
    modes.add_argument("--directory", type=Path, help="inspect an exported source tree without Git")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Git repository (default: cwd)")
    args = parser.parse_args(argv)
    root = (args.directory or args.repo).resolve()
    count = total = 0
    try:
        if not root.is_dir():
            raise ValueError("source directory does not exist")
        if args.directory is None:
            root = Path(git(root, "rev-parse", "--show-toplevel").decode().strip())
        files = historical_files(root) if args.all_history else working_files(root, args.directory is not None)
        for path, data, revision in files:
            total += 1
            for finding in check_content(path, data, revision):
                count += 1
                print(finding.render())
    except (OSError, ValueError, subprocess.CalledProcessError):
        # Git or filesystem diagnostics may contain private absolute paths.
        print("Public source check could not complete; verify the repository, file access, and full history.",
              file=sys.stderr)
        return 2
    print(f"Public source check: {total} files/blob paths checked; {count} finding(s).")
    return 1 if count else 0


if __name__ == "__main__":
    raise SystemExit(main())
