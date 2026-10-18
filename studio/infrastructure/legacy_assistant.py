"""Keep the existing persistent conversation, snapshots, model transport and CAS rules."""

from studio import image_provider, model_routing, models, profiles
from studio.adapters import DeliveryError
from studio.service import Problem


class LegacyAssistant:
    def __init__(self, studio):
        self.studio = studio

    def get(self, article_id):
        return self.studio.editor_assistant.get("post", article_id)

    def send(self, article_id, values):
        return self.studio.editor_assistant.send("post", article_id, values)

    def _owner(self, article_id, turn_id):
        row = self.studio.db.one(
            "SELECT kind,target_id FROM editor_assistant_turns WHERE id=?", (turn_id,)
        )
        if not row or row["kind"] != "post" or row["target_id"] != article_id:
            raise Problem("这条建议不属于当前文章", 404)

    def accept(self, article_id, turn_id, values):
        self._owner(article_id, turn_id)
        return self.studio.editor_assistant.accept(turn_id, values)

    def retry(self, article_id, turn_id):
        self._owner(article_id, turn_id)
        return self.studio.editor_assistant.retry(turn_id)

    def configuration(self):
        config = self.studio.config
        return {
            "configured": config.get("AI_MODE", "api") == "api"
            and bool(config.get("AI_BASE_URL") and config.get("AI_MODEL")),
            "discussion_model": config.get("AI_CHAT_MODEL") or config.get("AI_MODEL"),
            "editing_model": config.get("AI_BODY_MODEL") or config.get("AI_MODEL"),
            "reuse": getattr(self.studio, "configuration_reuse", {"status": "not_requested"}),
            "image_model": image_provider.configuration(config)["IMAGE_MODEL"],
        }

    def settings(self):
        return {
            **model_routing.public_settings(self.studio.config),
            "profiles": [
                {
                    "id": p["id"],
                    "name": p["name"],
                    "visual_style": p.get("visual_style", ""),
                    "reference_count": len(p.get("visual_references", [])),
                }
                for p in profiles.listing(self.studio)
            ],
        }

    def save_settings(self, values):
        model_routing.save(self.studio.config, values)
        return self.settings()

    def models(self, kind):
        try:
            return self._models(kind)
        except DeliveryError as exc:
            raise Problem(str(exc)) from None

    def _models(self, kind):
        config = self.studio.config
        if kind == "image":
            values = image_provider.configuration(config)

            class ImageConfig:
                def get(self, key, default=""):
                    return values.get(
                        {"AI_BASE_URL": "IMAGE_BASE_URL", "AI_API_KEY": "IMAGE_API_KEY"}.get(
                            key, key
                        ),
                        default,
                    )

            result = models.available(ImageConfig(), {})
        else:
            result = models.available(config, {})
        # Catalog identifiers are not capability or successful-call claims.
        return {"models": result["models"]}
