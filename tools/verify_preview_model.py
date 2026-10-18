"""Explicit isolated M2 smoke check using same-machine saved model settings."""

import argparse
import hashlib
import json
import time
from pathlib import Path

from studio.config import Config
from studio.db import dumps, uid
from studio.infrastructure.configuration_reuse import (
    finish_configuration,
    prepare_configuration,
    stable_settings_dir,
)
from studio.service import Studio


def verify(data, output, selected_models=False):
    source = stable_settings_dir() / "private.json"
    original = hashlib.sha256(source.read_bytes()).hexdigest()
    prepare_configuration(data)
    app = Studio(data)
    checks = []
    try:
        receipt = finish_configuration(app)
        assert receipt["complete"]
        article = app.save_post(
            {
                "title": "M2模型验收合成稿",
                "body": "这是一篇合成测试稿。我们先保存，再预览修改建议。",
            }
        )
        for mode, message in [
            ("discuss", "仅用一句话指出这段表达可改善的地方，不修改正文。"),
            ("edit", "保持标题，只将正文第二句改得更简洁，保留事实；返回完整建议正文。"),
        ]:
            source_state = app.editor_assistant.get("post", article["id"])
            turn_id = uid()
            app.editor_assistant.send(
                "post",
                article["id"],
                {
                    "request_id": turn_id,
                    "revision": source_state["revision"],
                    "message": message,
                    "mode": mode,
                    **(
                        {"model": app.config.get("AI_BODY_MODEL") or app.config.get("AI_MODEL")}
                        if selected_models and mode == "discuss"
                        else {}
                    ),
                    "draft": source_state["content"],
                },
            )
            if selected_models and mode == "discuss":
                route = json.loads(
                    app.db.one("SELECT route FROM editor_assistant_turns WHERE id=?", (turn_id,))[
                        "route"
                    ]
                )
                assert route["model"] == (
                    app.config.get("AI_BODY_MODEL") or app.config.get("AI_MODEL")
                )
            started = time.monotonic()
            assert app.editor_assistant.tick()
            turn = app.editor_assistant.get("post", article["id"])["turns"][-1]
            assert turn["status"] == "review", (
                "Real model call did not complete; see isolated task record."
            )
            assert bool(turn["result"]["reply"])
            if mode == "discuss":
                assert turn["result"]["replacement"] is None
            else:
                assert turn["result"]["replacement"]["body"]
                app.editor_assistant.accept(turn_id, {"draft": source_state["content"]})
            assert app.post(article["id"])["body"] == article["body"]
            checks.append(
                {"mode": mode, "passed": True, "seconds": round(time.monotonic() - started, 2)}
            )
        assert not app.jobs()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == original
        output.write_text(
            dumps(
                {
                    "passed": True,
                    "checks": checks,
                    "configuration_reused": True,
                    "original_unchanged": True,
                    "saved_article_unchanged": True,
                    "publishing_jobs": 0,
                    "selected_model_verified": selected_models,
                    "imported_profile_count": receipt.get("profiles_imported", 0),
                    "image_model_matches_original": app.config.get("IMAGE_MODEL")
                    == Config(stable_settings_dir()).get("IMAGE_MODEL"),
                }
            ),
            encoding="utf-8",
        )
    finally:
        app.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--isolated-data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--verify-selection", action="store_true")
    args = parser.parse_args()
    if args.isolated_data.exists():
        parser.error("Use a fresh isolated data directory.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    verify(args.isolated_data, args.output, args.verify_selection)
