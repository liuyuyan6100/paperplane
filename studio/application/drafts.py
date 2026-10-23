"""Use the existing delivery gate and worker through one adapter."""
from studio.domain.drafts import DraftPort


class DraftService:
    def __init__(self, port: DraftPort):
        self.port = port

    def get(self, article_id):
        return self.port.get(article_id)

    def command(self, article_id, action, values):
        return self.port.command(article_id, action, values)
