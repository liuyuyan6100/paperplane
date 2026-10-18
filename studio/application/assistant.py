"""Assistant application service. Suggestions only fill the editor after authorization."""

from studio.domain.assistant import AssistantPort


class AssistantService:
    def __init__(self, port: AssistantPort):
        self.port = port

    def get(self, article_id):
        return self.port.get(article_id)

    def send(self, article_id, values):
        return self.port.send(article_id, values)

    def accept(self, article_id, turn_id, values):
        return self.port.accept(article_id, turn_id, values)

    def retry(self, article_id, turn_id):
        return self.port.retry(article_id, turn_id)

    def configuration(self):
        return self.port.configuration()
