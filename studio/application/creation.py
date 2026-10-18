"""Explicit creation use cases; the existing workflow owns execution."""

from studio.domain.creation import CreationPort


class CreationService:
    def __init__(self, port: CreationPort):
        self.port = port

    def materials(self, query):
        return self.port.materials(query)

    def shared(self, query):
        return self.port.shared(query)

    def material(self, values):
        return self.port.material(values)

    def listing(self, post_id):
        return self.port.listing(post_id)

    def get(self, post_id, task_id):
        return self.port.get(post_id, task_id)

    def create(self, post_id, values):
        return self.port.create(post_id, values)

    def change(self, post_id, task_id, action, values):
        return self.port.change(post_id, task_id, action, values)
