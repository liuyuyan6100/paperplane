"""M1 uses synthetic data and loopback HTTP only."""

import json
import os
import subprocess
from pathlib import Path

import httpx
import pytest

from studio.preview_runtime import (
    PreviewService,
    preview_data_dir,
    validate_preview_data_dir,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def preview(tmp_path_factory):
    service = PreviewService(tmp_path_factory.mktemp("m1-editor") / "isolated")
    service.start()
    yield service
    service.stop()


@pytest.fixture
def client(preview):
    with httpx.Client(base_url=preview.origin, trust_env=False, timeout=10) as client:
        client.headers["X-Studio-Token"] = client.get("/api/v2/session").json()["token"]
        yield client


def test_save_preserves_legacy_history_and_fields(client, preview):
    first = client.post(
        "/api/v2/articles",
        json={
            "title": "M1合成稿",
            "body": "中文😀\n**正文**",
            "tags": ["回归"],
            "author": "合成作者",
            "source": "https://example.com/reference",
        },
    ).json()
    assert first["stage"] == "draft"
    result = client.post(
        "/api/v2/articles/" + first["id"],
        json={
            "title": "M1第二版",
            "body": "保留图片 ![图](/media/" + "a" * 32 + ".png)",
            "version": first["version"],
        },
    )
    assert result.status_code == 200
    assert result.json()["author"] == "合成作者"
    assert result.json()["version"] == 2
    assert (
        preview.app.db.one("SELECT COUNT(*) AS n FROM revisions WHERE post_id=?", (first["id"],))[
            "n"
        ]
        == 2
    )
    legacy_posts = client.get("/api/state").json()["posts"]
    assert (
        next(row for row in legacy_posts if row["id"] == first["id"])["body"]
        == result.json()["body"]
    )
    assert preview.server.studio is preview.app


def test_stale_version_rejects_without_extra_revision(client, preview):
    first = client.post("/api/v2/articles", json={"title": "冲突样稿", "body": "旧正文"}).json()
    route = "/api/v2/articles/" + first["id"]
    assert (
        client.post(route, json={"title": "已保存", "body": "另一窗口", "version": 1}).status_code
        == 200
    )
    result = client.post(route, json={"title": "过期写入", "body": "不能覆盖", "version": 1})
    assert result.status_code == 409
    assert client.get(route).json()["body"] == "另一窗口"
    assert (
        preview.app.db.one("SELECT COUNT(*) AS n FROM revisions WHERE post_id=?", (first["id"],))[
            "n"
        ]
        == 2
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"X-Studio-Token": "invalid"},
    ],
)
def test_rejects_untrusted_mutation_without_writing(client, preview, headers):
    count = preview.app.db.one("SELECT COUNT(*) AS n FROM posts")["n"]
    assert (
        client.post("/api/v2/articles", json={"title": "拒绝的写入"}, headers=headers).status_code
        == 403
    )
    assert preview.app.db.one("SELECT COUNT(*) AS n FROM posts")["n"] == count


def test_legacy_bridge_cannot_bypass_security_and_keeps_media_limits(client):
    assert client.get("/api/state", headers={"Host": "evil.example"}).status_code == 403
    assert (
        client.post(
            "/api/posts", json={"title": "无令牌"}, headers={"X-Studio-Token": "wrong"}
        ).status_code
        == 403
    )
    response = client.post("/api/posts", json={"title": "兼容入口新稿", "body": "同一数据库"})
    assert response.status_code == 200
    assert client.get("/api/v2/articles/" + response.json()["id"]).json()["body"] == "同一数据库"
    assert client.post("/api/shutdown", json={}).status_code == 403
    assert client.get("/api/desktop/update").status_code == 403


def test_json_validation_security_headers_and_preview(client):
    assert (
        client.post(
            "/api/v2/articles", content="{}", headers={"Content-Type": "text/plain"}
        ).status_code
        == 415
    )
    assert client.post("/api/v2/articles", json={"title": "a" * 151}).status_code == 422
    assert (
        client.post("/api/v2/articles", json={"title": "合成", "version": True}).status_code == 422
    )
    assert (
        client.post("/api/v2/articles", json={"title": "合成", "unexpected": "private"}).status_code
        == 422
    )
    assert (
        client.post(
            "/api/v2/articles",
            content="not-json",
            headers={"Content-Type": "application/json"},
        ).status_code
        == 422
    )
    response = client.post(
        "/api/v2/preview",
        json={"body": "# 标题\n<script>alert(1)</script>\n[危险](javascript:alert)"},
    )
    assert response.status_code == 200
    assert "<script>" not in response.json()["html"]
    assert "javascript:" not in response.json()["html"]
    assert "object-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_chunked_request_limit_and_invalid_id(client):
    response = client.post(
        "/api/v2/articles",
        content=iter([b"x" * 1000000] * 11),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
    assert client.get("/api/v2/articles/not-an-id").status_code == 422
    assert client.get("/api/v2/missing").status_code == 404


def test_assets_cannot_escape_frontend(client):
    assert client.get("/editor/").status_code == 200
    assert client.get("/editor/%2e%2e/%2e%2e/AGENTS.md").status_code == 404
    assert client.get("/api/v2/session").json()["milestone"] == "M3"


def test_preview_data_is_separate_and_stable_path_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert preview_data_dir() == tmp_path / "PaperPlaneStudioPreview/local"
    for path in (
        tmp_path,
        tmp_path / "PaperPlaneStudio",
        tmp_path / "PaperPlaneStudio/local",
    ):
        with pytest.raises(ValueError):
            validate_preview_data_dir(path)
    assert validate_preview_data_dir(tmp_path / "preview-data").name == "preview-data"


def test_restart_persistence_and_one_writer_lock(tmp_path):
    path = tmp_path / "restart-data"
    service = PreviewService(path)
    service.start()
    article = service.app.save_post({"title": "重开后仍在", "body": "原始内容"})
    other = PreviewService(path)
    try:
        with pytest.raises(RuntimeError):
            other.start()
        assert service.active_tasks() is False
    finally:
        service.stop()
    service = PreviewService(path)
    try:
        service.start()
        assert service.app.post(article["id"])["body"] == "原始内容"
    finally:
        service.stop()


def test_editor_real_browser(preview, tmp_path):
    fixture = tmp_path / "browser.json"
    fixture.write_text(
        json.dumps({"url": preview.origin, "output": str(ROOT / "test-results/m1-browser")}),
        encoding="utf-8",
    )
    result = subprocess.run(
        ["node", str(ROOT / "tests/verify_preview_editor.cjs"), str(fixture)],
        cwd=ROOT,
        env={**os.environ},
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
