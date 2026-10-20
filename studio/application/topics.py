"""Topic use cases delegate snapshots and idempotent selection to the existing manager."""

from studio.domain.topics import TopicPort


class TopicService:
    def __init__(self, port: TopicPort):
        self.port = port

    def listing(self):
        return self.port.listing()

    def get(self, plan_id):
        return self.port.get(plan_id)

    def create(self, values):
        return self.port.create(values)

    def retry(self, plan_id):
        return self.port.retry(plan_id)

    def select(self, plan_id, index):
        return self.port.select(plan_id, index)
