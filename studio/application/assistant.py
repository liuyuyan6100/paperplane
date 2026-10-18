"""Assistant application service. Suggestions only fill the editor after authorization."""

from studio.domain.assistant import AssistantPort


class AssistantService:
    def __init__(self, port: AssistantPort):
        self.port = port

    def get(self, article_id):
        return self.port.get(article_id)

    def metadata(self, article_id):
        return self.port.metadata(article_id)

    def suggest_metadata(self, article_id, values):
        return self.port.suggest_metadata(article_id, values)

    def accept_metadata(self, article_id, turn_id, values):
        return self.port.accept_metadata(article_id, turn_id, values)

    def send(self, article_id, values):
        return self.port.send(article_id, values)

    def accept(self, article_id, turn_id, values):
        return self.port.accept(article_id, turn_id, values)

    def retry(self, article_id, turn_id):
        return self.port.retry(article_id, turn_id)

    def configuration(self):
        return self.port.configuration()

    def settings(self):
        return self.port.settings()

    def save_settings(self, values):
        return self.port.save_settings(values)

    def models(self, kind):
        return self.port.models(kind)
