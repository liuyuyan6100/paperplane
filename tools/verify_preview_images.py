"""Explicit paid image smoke test in a fresh isolated directory using saved settings."""

import argparse
import base64
import hashlib
import json
import shutil
import sqlite3
import time
from pathlib import Path

from PIL import Image

from studio import profiles
from studio.db import uid
from studio.infrastructure.legacy_illustrations import LegacyIllustrations
from studio.service import Studio


def verify(source, data, output):
    data.mkdir(parents=True, exist_ok=False)
    original = hashlib.sha256((source / "private.json").read_bytes()).hexdigest()
    shutil.copy2(source / "private.json", data / "private.json")
    app = Studio(data)
    report = {"passed": False, "checks": [], "isolated": True, "external_delivery": 0}
    try:
        profile_id = None
        with sqlite3.connect((source / "studio.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            row = db.execute("SELECT content FROM profiles ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row:
            content = json.loads(row[0])
            references = []
            for url in content.get("visual_references", []):
                raw = (source / "media" / Path(url).name).read_bytes()
                references.append(app.upload({"data": base64.b64encode(raw).decode(), "title": "验收复用参考图"})["url"])
            profile_id = profiles.save(app, {"name": "隔离验收视觉风格", "visual_style": content.get("visual_style", ""), "visual_references": references})["id"]
        post = app.save_post({"title": "把检查点放进创作流程", "stage": "draft", "body": "## 保存与检查\n\n把素材、提纲和候选结果放在清晰的检查点上，经过审阅再采用。\n\n## 保留修改\n\n人工修改和历史版本保留，失败后可以核对原输入再重新尝试。", "profile_id": profile_id})
        port = LegacyIllustrations(app)
        plan = port.create(post["id"], {"post_version": 1, "request_id": uid()})
        for index in (0, 1):
            candidate_id = uid()
            started = time.monotonic()
            port.change(post["id"], plan["id"], "generate", {"version": plan["version"], "request_id": candidate_id, "page_index": index, "instruction": "简洁概念插画，使用留白和清晰关系，保持参考图风格。", "parent_id": None})
            assert app.illustrations.tick()
            current = port.get(post["id"], plan["id"])
            candidate = next(c for c in current["candidates"] if c["id"] == candidate_id)
            assert candidate["status"] == "review", candidate["error"]
            assert app.post(post["id"])["version"] == index + 1
            path = data / "media" / Path(candidate["image_url"]).name
            with Image.open(path) as image:
                size = list(image.size)
                image.verify()
            values = {"candidate_id": candidate_id, "post_version": index + 1,
                      "placement": current["pages"][index].get("placement", ""), "caption": "", "ack_changes": False}
            port.change(post["id"], plan["id"], "preview", values)
            saved = port.change(post["id"], plan["id"], "adopt", values)
            assert saved["post"]["version"] == index + 2
            report["checks"].append({"slot": current["pages"][index]["slot"], "status": "passed", "seconds": round(time.monotonic() - started, 2), "bytes": path.stat().st_size, "size": size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "image": str(path), "model": candidate["model_provenance"]["requested_model"], "references": len(current["references"])})
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        assert not app.jobs()
        assert hashlib.sha256((source / "private.json").read_bytes()).hexdigest() == original
        report.update(passed=True, settings_source_unchanged=True, adopted_version=3)
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        app.close()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "checks"}, ensure_ascii=False))
    assert report["passed"], "Real image smoke check did not complete; see isolated result."


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings-dir", type=Path, required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    verify(args.settings_dir.resolve(), args.data_dir.resolve(), args.output.resolve())
