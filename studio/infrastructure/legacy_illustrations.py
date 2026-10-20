"""Narrow adapter to persisted plans, real references and atomic image adoption."""

import base64
import json
import threading

from studio.adapters import DeliveryError
from studio.image_provider import image_bytes
from studio.service import Problem


class LegacyIllustrations:
    def __init__(self, app):
        self.app = app
        self.manager = app.illustrations
        self.lock = threading.RLock()

    def owned(self, post_id, plan_id):
        plan = self.manager.get(plan_id)
        if plan["post_id"] != post_id:
            raise Problem("本篇配图计划不存在", 404)
        return plan

    @staticmethod
    def present(plan):
        fields = (
            "id", "post_id", "source_version", "latest_post_version", "post_version",
            "status", "version", "archived_at", "error", "created_at", "updated_at",
            "source_changed", "profile_changed", "pages", "placement_options", "adopted",
            "model_provenance", "next_models",
        )
        result = {key: plan[key] for key in fields}
        profile = plan["context"].get("profile") or {}
        result["profile"] = {
            key: profile.get(key, "") for key in ("id", "name", "version", "visual_style")
        }
        result["references"] = [r["url"] for r in plan["context"].get("references", [])]
        result["candidates"] = [
            {**{key: c[key] for key in (
                "id", "page_index", "status", "image_url", "error", "created_at",
                "plan_changed", "origin", "model_provenance",
            )}, "page": c["context"]["page"],
                "instruction": c["context"].get("instruction", "")}
            for c in plan["candidates"]
        ]
        return result

    def listing(self, post_id, archived=False):
        self.app.post(post_id)
        return {"plans": self.manager.listing(post_id, archived)}

    def get(self, post_id, plan_id):
        return self.present(self.owned(post_id, plan_id))

    def create(self, post_id, values):
        with self.lock:
            prior = self.app.db.one(
                "SELECT post_id,source_version FROM illustration_plans WHERE id=?",
                (values["request_id"],),
            )
            if prior and (prior["post_id"] != post_id or prior["source_version"] != values["post_version"]):
                raise Problem("请求编号已用于不同的文章版本", 409)
            return self.present(self.manager.create({**values, "post_id": post_id}))

    def change(self, post_id, plan_id, action, values):
        with self.lock:
            plan = self.owned(post_id, plan_id)
            if action == "generate":
                existing = self.app.db.one(
                    "SELECT plan_id,snapshot FROM illustration_candidates WHERE id=?",
                    (values["request_id"],),
                )
                if existing:
                    snapshot = json.loads(existing["snapshot"])
                    parent = next((c for c in plan["candidates"] if c["id"] == values.get("parent_id")), None)
                    if (
                        existing["plan_id"] != plan_id
                        or snapshot.get("origin", "generated") != "generated"
                        or snapshot["plan_version"] != values["version"]
                        or snapshot["page_index"] != values["page_index"]
                        or snapshot.get("instruction", "") != values.get("instruction", "")
                        or snapshot.get("parent_image", "") != (parent["image_url"] if parent else "")
                    ):
                        raise Problem("请求编号已用于不同的候选图要求", 409)
            result = getattr(self.manager, action)(plan_id, values)
            if action == "preview":
                return result
            if action == "adopt":
                return {**result, "post": self.app.post(post_id), "plan": self.get(post_id, plan_id)}
            return self.present(result)

    def upload(self, values):
        try:
            image_bytes(base64.b64decode(values["data"], validate=True))
        except (ValueError, DeliveryError):
            raise Problem("请选择5 MB以内的有效PNG、JPEG或WebP原图") from None
        return self.app.upload({**values, "origin": "upload"})
