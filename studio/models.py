"""Read a configured API's model catalog without changing saved settings."""
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .adapters import DeliveryError, NoRedirect

MAX_RESPONSE = 1_000_000


def endpoint(value):
    if not isinstance(value, str) or len(value) > 2000 or any(ord(c) < 33 for c in value.strip()):
        raise DeliveryError("模型接口地址格式不正确")
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        valid = (parsed.hostname and not parsed.username and not parsed.password
                 and not parsed.query and not parsed.fragment and parsed.port != 0
                 and (parsed.scheme == "https" or parsed.scheme == "http"
                      and parsed.hostname in ("127.0.0.1", "localhost", "::1")))
    except ValueError:
        valid = False
    if not valid:
        raise DeliveryError("接口地址需要 HTTPS，本机模型可使用 HTTP；不要附带账号或查询参数")
    return value


def available(config, values):
    saved_base = config.get("AI_BASE_URL")
    base = endpoint(values.get("base_url", saved_base))
    key = values.get("api_key", "")
    if not isinstance(key, str) or len(key) > 2000 or any(ord(c) < 32 for c in key):
        raise DeliveryError("API Key 格式不正确")
    key = key.strip()
    # Never send a saved credential to a newly typed destination.
    if not key and base != str(saved_base).strip().rstrip("/"):
        raise DeliveryError("接口地址已改变，请填写新接口的 API Key；无需认证的接口请先保存地址")
    if not key:
        key = config.get("AI_API_KEY")
    request = urllib.request.Request(base + "/models", headers={
        "Accept": "application/json", "User-Agent": "PaperPlaneStudio/1.0",
        **({"Authorization": "Bearer " + key} if key else {}),
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=15) as response:
            raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise DeliveryError("模型列表响应过大，请手动填写模型名称")
        payload = json.loads(raw)
    except urllib.error.HTTPError as exc:
        messages = {401: "凭据无效，请检查模型 API Key", 403: "当前密钥没有读取模型列表的权限",
                    404: "接口未提供模型列表，请手动填写模型名称", 429: "请求频率受限，请稍后获取"}
        raise DeliveryError(messages.get(exc.code, f"获取模型列表失败：HTTP {exc.code}")) from None
    except (OSError, ValueError):
        raise DeliveryError("模型列表请求失败或响应不是有效 JSON，请检查接口后重试；仍可手动填写") from None
    items = payload.get("data", payload.get("models")) if isinstance(payload, dict) else payload
    if not isinstance(items, list) or len(items) > 5000:
        raise DeliveryError("模型列表格式不兼容，请手动填写模型名称")
    ids = set()
    for item in items:
        value = item.get("id") if isinstance(item, dict) else item
        if isinstance(value, str) and value.strip() and len(value) <= 2000 and all(ord(c) >= 32 for c in value):
            ids.add(value.strip())
    return {"models": sorted(ids), "base_url": base}
