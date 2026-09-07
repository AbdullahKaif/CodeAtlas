"""Every SQL statement in the application lives here."""
from datetime import datetime, timezone

from taskflow.db.connection import connect
from taskflow.models import Task, User


def find_user_by_name(username: str) -> User | None:
    """Look up a user with a parameterised query (the safe way)."""
    connection = connect()
    row = connection.execute(
        "SELECT id, username, password_hash, is_admin FROM users WHERE username = ?", (username,)
    ).fetchone()
    connection.close()
    if row is None:
        return None
    return User(id=row[0], username=row[1], password_hash=row[2], is_admin=bool(row[3]))


def find_tasks_by_owner(owner: str) -> list[Task]:
    """DELIBERATE DEFECT: the owner is concatenated into the SQL (SQL injection)."""
    connection = connect()
    query = "SELECT id, owner, title, done, created_at FROM tasks WHERE owner = '" + owner + "' ORDER BY id"
    rows = connection.execute(query).fetchall()
    connection.close()
    return [_task_from_row(row) for row in rows]


def insert_task(owner: str, title: str) -> Task:
    """Insert a task and return it with its new id."""
    connection = connect()
    created_at = datetime.now(timezone.utc).isoformat()
    cursor = connection.execute(
        "INSERT INTO tasks (owner, title, done, created_at) VALUES (?, ?, 0, ?)", (owner, title, created_at)
    )
    connection.commit()
    task_id = cursor.lastrowid
    connection.close()
    return Task(id=task_id, owner=owner, title=title, created_at=datetime.fromisoformat(created_at))


def mark_task_done(owner: str, task_id: int) -> None:
    """Set done=1 for one of the owner's tasks."""
    connection = connect()
    connection.execute("UPDATE tasks SET done = 1 WHERE id = ? AND owner = ?", (task_id, owner))
    connection.commit()
    connection.close()


def _task_from_row(row) -> Task:
    return Task(
        id=row[0],
        owner=row[1],
        title=row[2],
        done=bool(row[3]),
        created_at=datetime.fromisoformat(row[4]),
    )
