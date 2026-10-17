"""Small, portable protocol shared by the private update server and client."""
import re
from urllib.parse import urlsplit

MAX_INSTALLER = 200 * 1024 * 1024


class UpdateError(Exception):
    pass


def version(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,8}\.\d{1,8}\.\d{1,8}", value):
        raise UpdateError("发布版本号不正确")
    return tuple(map(int, value.split(".")))


def server_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 32 for c in value):
        raise UpdateError("更新服务器地址不正确")
    value = value.strip().rstrip("/")
    parsed = urlsplit(value)
    try:
        parsed.port
    except ValueError:
        raise UpdateError("更新服务器端口不正确") from None
    local = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
    if (not parsed.hostname or not (parsed.scheme == "https" or local) or parsed.username or parsed.password
            or parsed.path or parsed.query or parsed.fragment):
        raise UpdateError("请填写 HTTPS 更新服务器首页地址；仅本机测试允许 HTTP")
    return value


def release_info(payload, base):
    base = server_url(base)
    if not isinstance(payload, dict) or payload.get("schema") != 1 or payload.get("published") is not True or payload.get("platform") != "windows-x64":
        raise UpdateError("当前发布不是可安装的 Windows 正式版本")
    number = payload.get("version")
    version(number)
    name = f"PaperPlaneStudio-Setup-{number}.exe"
    asset = payload.get("installer")
    if not isinstance(asset, dict) or asset.get("name") != name:
        raise UpdateError("发布缺少对应版本的 Windows 安装包")
    size, digest = asset.get("size"), asset.get("sha256")
    if type(size) is not int or not 2 <= size <= MAX_INSTALLER:
        raise UpdateError("安装包大小不正确")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
        raise UpdateError("发布缺少安装包校验信息")
    return {"version": number, "name": name, "size": size, "sha256": digest.lower(),
            "url": base + f"/v1/installers/{number}/{name}", "notes": str(payload.get("notes") or "本版本未填写更新说明")[:20000]}
