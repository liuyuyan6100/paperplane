"""Explicit use cases; existing image manager and worker retain execution."""

from studio.domain.illustrations import IllustrationPort


class IllustrationService:
    def __init__(self, port: IllustrationPort):
        self.port = port

    def listing(self, post_id, archived):
        return self.port.listing(post_id, archived)

    def get(self, post_id, plan_id):
        return self.port.get(post_id, plan_id)

    def create(self, post_id, values):
        return self.port.create(post_id, values)

    def change(self, post_id, plan_id, action, values):
        return self.port.change(post_id, plan_id, action, values)

    def upload(self, values):
        return self.port.upload(values)
