"""Optional WeChat fixed egress, owned by Studio; only standard libraries."""
import http.client
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time
import urllib.request
from urllib.parse import urlsplit

from .adapters import DeliveryError, NoRedirect

DEFAULTS = {"WECHAT_PROXY": "", "WECHAT_SSH_HOST": "", "WECHAT_SSH_KEY": "",
            "WECHAT_SSH_HOST_ALIAS": "", "WECHAT_SSH_PORT": "1281"}
_processes = {}
_lock = threading.Lock()


def validate_route(config):
    proxy = config.get("WECHAT_PROXY", "")
    if proxy:
        try:
            parsed = urlsplit(proxy)
            valid = (parsed.scheme in ("http", "socks5h") and parsed.hostname in ("127.0.0.1", "localhost", "::1")
                     and parsed.port and not parsed.username and not parsed.password
                     and parsed.path in ("", "/") and not parsed.query and not parsed.fragment)
        except ValueError:
            valid = False
        if not valid:
            raise DeliveryError("公众号代理需要本机 http://127.0.0.1:端口 或 socks5h://127.0.0.1:端口")
    host = config.get("WECHAT_SSH_HOST", "")
    if host:
        if proxy:
            raise DeliveryError("固定出口和已有代理请选择一种")
        if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*@[A-Za-z0-9][A-Za-z0-9.-]*", host):
            raise DeliveryError("固定出口主机格式为 用户名@主机地址")
        alias = config.get("WECHAT_SSH_HOST_ALIAS", "")
        if alias and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", alias):
            raise DeliveryError("主机校验名称格式不正确")
        key = config.get("WECHAT_SSH_KEY", "")
        if not key or not Path(key).is_file():
            raise DeliveryError("请选择本机已有的 SSH 私钥文件；不会复制或上传私钥")
        try:
            if not 1024 <= int(config.get("WECHAT_SSH_PORT", "1281")) <= 65535:
                raise ValueError()
        except (ValueError, TypeError):
            raise DeliveryError("固定出口的本机端口需要在 1024–65535 之间") from None


def _alive(port):
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=.4):
            return True
    except OSError:
        return False


def _managed_proxy(config):
    validate_route(config)
    if not config.get("WECHAT_SSH_HOST", ""):
        return config.get("WECHAT_PROXY", "")
    host, key, alias, port = (config.get("WECHAT_SSH_HOST"), config.get("WECHAT_SSH_KEY"),
                              config.get("WECHAT_SSH_HOST_ALIAS", ""), int(config.get("WECHAT_SSH_PORT", "1281")))
    identity = host, key, alias, port
    with _lock:
        process = _processes.get(identity)
        if process and process.poll() is None and _alive(port):
            return f"socks5h://127.0.0.1:{port}"
        if _alive(port):
            raise DeliveryError("固定出口的本机端口已被占用，请换一个端口或选择已有代理")
        executable = shutil.which("ssh")
        if not executable:
            raise DeliveryError("本机未找到 OpenSSH 客户端，请安装或配置已有代理")
        args = [executable, "-F", os.devnull, "-o", "PermitLocalCommand=no", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ExitOnForwardFailure=yes",
                "-o", "StrictHostKeyChecking=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3"]
        if alias:
            args += ["-o", "HostKeyAlias=" + alias]
        args += ["-i", key, "-D", f"127.0.0.1:{port}", "-N", host]
        try:
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                       shell=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            _processes[identity] = process
            deadline = time.monotonic() + 9
            while time.monotonic() < deadline and process.poll() is None:
                if _alive(port):
                    return f"socks5h://127.0.0.1:{port}"
                time.sleep(.1)
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass
    raise DeliveryError("固定出口连接失败，请核对主机、私钥权限与 known_hosts；平台不会跳过主机身份校验")


def close_routes():
    with _lock:
        for process in _processes.values():
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
        _processes.clear()


def _receive(connection, count):
    result = b""
    while len(result) < count:
        part = connection.recv(count - len(result))
        if not part:
            raise OSError("SOCKS connection closed")
        result += part
    return result


def _socks_socket(proxy, host, port, timeout):
    connection = socket.create_connection((proxy.hostname, proxy.port), timeout=timeout)
    try:
        connection.sendall(b"\x05\x01\x00")
        if _receive(connection, 2) != b"\x05\x00":
            raise OSError("SOCKS authentication unsupported")
        domain = host.encode("idna")
        if len(domain) > 255:
            raise OSError("Host too long")
        connection.sendall(b"\x05\x01\x00\x03" + bytes([len(domain)]) + domain + port.to_bytes(2, "big"))
        response = _receive(connection, 4)
        if response[:3] != b"\x05\x00\x00":
            raise OSError("SOCKS destination unavailable")
        size = {1: 4, 4: 16}.get(response[3])
        if response[3] == 3:
            size = _receive(connection, 1)[0]
        if size is None:
            raise OSError("Invalid SOCKS address")
        _receive(connection, size + 2)
        return connection
    except BaseException:
        connection.close()
        raise


class _SocksHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, proxy, **kwargs):
        self.proxy = proxy
        super().__init__(host, **kwargs)

    def connect(self):
        connection = _socks_socket(self.proxy, self.host, self.port, self.timeout)
        try:
            self.sock = self._context.wrap_socket(connection, server_hostname=self.host)
        except BaseException:
            connection.close()
            raise


class _SocksHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, proxy):
        super().__init__()
        self.proxy = proxy

    def https_open(self, request):
        return self.do_open(lambda host, **kwargs: _SocksHTTPSConnection(host, self.proxy, context=ssl.create_default_context(), **kwargs), request)


def build_wechat_opener(config):
    proxy = _managed_proxy(config)
    parsed = urlsplit(proxy)
    if parsed.scheme == "socks5h":
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(), _SocksHTTPSHandler(parsed))
    return urllib.request.build_opener(urllib.request.ProxyHandler({"https": proxy} if proxy else {}), NoRedirect())
