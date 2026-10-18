"""ASGI composition root, same-origin security and temporary legacy HTTP adapter."""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response

from studio.api.articles import article_router
from studio.api.assistant import assistant_router
from studio.application.articles import ArticleService
from studio.application.assistant import AssistantService
from studio.infrastructure.legacy_articles import LegacyArticleRepository
from studio.infrastructure.legacy_assistant import LegacyAssistant
from studio.service import Problem

PREVIEW_VERSION = "0.2.0-m2.2"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; "
    "img-src 'self' https: http: data:; media-src 'self'; connect-src 'self'; "
    "frame-ancestors 'self'; base-uri 'none'; form-action 'self'; object-src 'none'"
)


def create_app(studio, legacy_url, token, port, frontend=None):
    """One Studio/worker owns the data; the HTTP bridge never creates another."""
    frontend = Path(frontend or Path(__file__).resolve().parents[2] / "frontend/dist")
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {"http://" + host for host in hosts}
    legacy_host = legacy_url.removeprefix("http://")

    @asynccontextmanager
    async def lifespan(app):
        async with httpx.AsyncClient(trust_env=False, timeout=60, follow_redirects=False) as client:
            app.state.legacy_client = client
            yield

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        mutation = request.method not in ("GET", "HEAD")
        error, status = None, 403
        if request.headers.get("host") not in hosts:
            error = "请使用验证版本机访问地址"
        elif request.headers.get("origin") and request.headers["origin"] not in origins:
            error = "拒绝跨站请求"
        elif request.headers.get("sec-fetch-site") == "cross-site":
            error = "拒绝跨站请求"
        elif request.method not in ("GET", "HEAD", "POST"):
            error, status = "不支持此请求方法", 405
        elif mutation:
            if not secrets.compare_digest(request.headers.get("x-studio-token", ""), token):
                error = "页面会话已更新，请保留编辑内容并刷新"
            elif request.headers.get("content-type", "").split(";")[0] != "application/json":
                error, status = "请求必须使用 JSON", 415
            else:
                limit = (
                    44_750_000
                    if request.url.path == "/api/library/upload"
                    else 14_050_000
                    if request.url.path.endswith("/videos")
                    else 10_000_000
                )
                try:
                    length = int(request.headers.get("content-length", "0"))
                except ValueError:
                    length = -1
                if length < 0 or length > limit:
                    error, status = "请求大小无效或超过限制", 413
                else:
                    body = bytearray()
                    async for chunk in request.stream():
                        body.extend(chunk)
                        if len(body) > limit:
                            error, status = "请求超过大小限制", 413
                            break
                    if not error:
                        request._body = bytes(body)
        if error:
            response = JSONResponse({"error": error}, status_code=status)
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": CSP,
            }
        )
        return response

    @app.exception_handler(Problem)
    async def business_error(request, exc):
        return JSONResponse({"error": str(exc), "details": exc.details}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": "输入格式或长度不正确，请检查文章字段"}, status_code=422)

    app.include_router(article_router(ArticleService(LegacyArticleRepository(studio))))
    app.include_router(assistant_router(AssistantService(LegacyAssistant(studio))))

    @app.get("/api/v2/session")
    def session():
        return {
            "token": token,
            "version": PREVIEW_VERSION,
            "milestone": "M2",
            "data_dir": str(studio.db.data_dir),
            "legacy_url": "/",
        }

    @app.get("/preview-healthz")
    def health():
        return {
            "ok": True,
            "version": PREVIEW_VERSION,
            "worker_alive": bool(studio.worker and studio.worker.is_alive()),
        }

    @app.get("/editor")
    @app.get("/editor/{asset:path}")
    def editor(asset=""):
        target = frontend / (asset or "index.html")
        if (
            target.is_symlink()
            or not target.resolve().is_relative_to(frontend.resolve())
            or not target.is_file()
        ):
            return JSONResponse({"error": "页面资源不存在，请先构建前端"}, status_code=404)
        return FileResponse(target)

    @app.api_route("/{path:path}", methods=["GET", "HEAD", "POST"])
    async def legacy(request: Request, path: str):
        if path.startswith("api/v2/"):
            return JSONResponse({"error": "接口不存在"}, status_code=404)
        if path == "api/shutdown" or path.startswith("api/desktop/update"):
            return JSONResponse(
                {"error": "请通过验证版客户端退出；验证版不使用稳定版更新通道"},
                status_code=403,
            )
        headers = {
            k: v
            for k, v in request.headers.items()
            if k.lower() in {"content-type", "x-studio-token", "range", "sec-fetch-site"}
        }
        headers["Host"] = legacy_host
        if request.headers.get("origin"):
            headers["Origin"] = legacy_url
        try:
            result = await app.state.legacy_client.request(
                request.method,
                legacy_url + "/" + path,
                params=request.query_params,
                headers=headers,
                content=await request.body(),
            )
        except httpx.HTTPError:
            return JSONResponse({"error": "兼容服务暂时不可用，请保留编辑内容"}, status_code=503)
        allowed = {
            "content-type",
            "content-disposition",
            "content-range",
            "accept-ranges",
        }
        content = result.content
        if path == "" and "text/html" in result.headers.get("content-type", ""):
            content = content.replace(
                b"</head>", b'<link rel="stylesheet" href="/editor/compat.css"></head>', 1
            )
            content = content.replace(
                b"</body>",
                '<a class="preview-return" href="/editor/">\u2190 \u8fd4\u56de\u65b0\u7f16\u8f91\u5668</a></body>'.encode(),
                1,
            )
        return Response(
            content,
            status_code=result.status_code,
            headers={k: v for k, v in result.headers.items() if k.lower() in allowed},
        )

    return app
