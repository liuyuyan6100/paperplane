"""Publish immutable installers into a private Docker update server's volume."""
import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from desktop_version import VERSION
from release_protocol import MAX_INSTALLER, UpdateError, version, release_info


def atomic_json(path, payload):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        json.dump(payload, file, ensure_ascii=False, indent=2)
    try:
        temporary.chmod(0o644)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def publish(directory, package, number, notes):
    version(number)
    directory, package = Path(directory).resolve(), Path(package).resolve()
    name = f"PaperPlaneStudio-Setup-{number}.exe"
    if package.name != name or not 2 <= package.stat().st_size <= MAX_INSTALLER:
        raise UpdateError("安装包文件名或大小不正确")
    with package.open("rb") as file:
        if file.read(2) != b"MZ":
            raise UpdateError("安装包不是 Windows 可执行文件")
        file.seek(0)
        checksum = hashlib.file_digest(file, "sha256").hexdigest()
    payload = {"schema": 1, "version": number, "platform": "windows-x64", "published": True, "notes": notes[:20000],
               "installer": {"name": name, "size": package.stat().st_size, "sha256": checksum}}
    release_info(payload, "http://127.0.0.1")
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / ".publish.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise UpdateError("存在发布锁，请确认其他发布完成后重试；不要覆盖进行中的发布") from None
    try:
        latest = directory / "latest.json"
        if latest.exists():
            old = json.loads(latest.read_text(encoding="utf-8"))
            if version(number) < version(old["version"]):
                raise UpdateError("不允许把最新版本降到旧版本")
        folder = directory / number
        if folder.is_symlink():
            raise UpdateError("发布版本目录不能是符号链接")
        folder.mkdir(exist_ok=True)
        installer = folder / name
        metadata = folder / "release.json"
        if installer.exists():
            if not metadata.is_file() or json.loads(metadata.read_text(encoding="utf-8")) != payload:
                raise UpdateError("同一版本已存在不同内容，请递增版本号，不覆盖原包")
            with installer.open("rb") as file:
                if hashlib.file_digest(file, "sha256").hexdigest() != checksum:
                    raise UpdateError("已有安装包损坏，不允许直接覆盖，请核实后处理")
        else:
            with tempfile.NamedTemporaryFile(dir=folder, delete=False) as file:
                temporary = Path(file.name)
                with package.open("rb") as source:
                    shutil.copyfileobj(source, file)
            try:
                with temporary.open("rb") as file:
                    if hashlib.file_digest(file, "sha256").hexdigest() != checksum:
                        raise UpdateError("打包文件在发布过程中发生变化")
                temporary.chmod(0o644)
                os.replace(temporary, installer)
            finally:
                temporary.unlink(missing_ok=True)
            atomic_json(metadata, payload)
        atomic_json(latest, payload)
        return payload
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--version", default=VERSION)
    parser.add_argument("--package", type=Path)
    parser.add_argument("--notes", type=Path)
    args = parser.parse_args()
    package = args.package or ROOT / f"dist/installer/PaperPlaneStudio-Setup-{args.version}.exe"
    notes = args.notes or ROOT / f"docs/releases/desktop-{args.version}.md"
    data = publish(args.store, package, args.version, notes.read_text(encoding="utf-8"))
    print(json.dumps({"store": str(args.store.resolve()), "version": data["version"], "sha256": data["installer"]["sha256"]}))


if __name__ == "__main__":
    main()
