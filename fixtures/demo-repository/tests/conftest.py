"""Shared fixtures for the TaskFlow tests."""
import pytest

from taskflow.auth.service import AuthService
from taskflow.services.tasks import TaskService


@pytest.fixture
def auth_service() -> AuthService:
    return AuthService()


@pytest.fixture
def task_service() -> TaskService:
    return TaskService()
