"""Task use-cases."""
from taskflow.db.repository import find_tasks_by_owner, insert_task, mark_task_done
from taskflow.models import Task
from taskflow.services.notifications import notify_owner


class TaskService:
    """Creates, lists and completes tasks on behalf of a user."""

    def create(self, owner: str, title: str) -> Task:
        """Create a task; titles are trimmed and must not be empty."""
        title = title.strip()
        if not title:
            raise ValueError("Task title must not be empty")
        return insert_task(owner, title)

    def list_for(self, owner: str) -> list[Task]:
        """All tasks belonging to the owner, oldest first."""
        return find_tasks_by_owner(owner)

    def complete(self, owner: str, task_id: int) -> None:
        """Mark a task done and notify the owner."""
        mark_task_done(owner, task_id)
        notify_owner(owner, f"Task {task_id} completed")
