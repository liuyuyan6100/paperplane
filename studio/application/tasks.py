"""Task use cases; each existing owner retains its own state machine."""

from studio.domain.tasks import TaskRepository


class TaskService:
    def __init__(self, repository: TaskRepository):
        self.repository = repository

    def listing(self, **filters):
        return self.repository.listing(**filters)

    def detail(self, kind: str, task_id: str):
        return self.repository.detail(kind, task_id)

    def command(self, kind: str, task_id: str, values: dict):
        return self.repository.command(kind, task_id, values)
