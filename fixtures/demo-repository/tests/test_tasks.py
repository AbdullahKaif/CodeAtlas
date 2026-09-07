"""Task service tests."""
import pytest

from taskflow.services.tasks import TaskService


def test_empty_title_is_rejected(task_service: TaskService) -> None:
    with pytest.raises(ValueError):
        task_service.create("alice", "   ")
