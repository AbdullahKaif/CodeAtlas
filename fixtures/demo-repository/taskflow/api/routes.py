"""HTTP handlers. Each one authenticates, then delegates to a service."""
from taskflow.api.router import router
from taskflow.auth.service import AuthService
from taskflow.services.tasks import TaskService

auth = AuthService()
tasks = TaskService()


@router.post("/login")
def login(username: str, password: str) -> dict:
    """Exchange credentials for a session token."""
    token = auth.login(username, password)
    if token is None:
        return {"error": "invalid credentials"}
    return {"token": token}


@router.get("/tasks")
def list_tasks(token: str) -> dict:
    """List the caller's tasks."""
    username = auth.authenticate(token)
    if username is None:
        return {"error": "unauthorized"}
    return {"tasks": [t.title for t in tasks.list_for(username)]}


@router.post("/tasks")
def create_task(token: str, title: str) -> dict:
    """Create a task for the caller."""
    username = auth.authenticate(token)
    if username is None:
        return {"error": "unauthorized"}
    task = tasks.create(username, title)
    return {"id": task.id, "title": task.title}


@router.post("/tasks/complete")
def complete_task(token: str, task_id: int) -> dict:
    """Mark a task done and notify its owner."""
    username = auth.authenticate(token)
    if username is None:
        return {"error": "unauthorized"}
    tasks.complete(username, task_id)
    return {"done": True}
