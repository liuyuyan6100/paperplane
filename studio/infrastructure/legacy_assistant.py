"""Keep the existing persistent conversation, snapshots, model transport and CAS rules."""

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
        }
