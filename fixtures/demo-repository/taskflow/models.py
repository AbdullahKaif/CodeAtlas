"""Plain records shared by every layer."""
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class User:
    """An account that can own tasks."""

    id: int
    username: str
    password_hash: str
    is_admin: bool = False


@dataclass
class Task:
    """A unit of work owned by one user."""

    id: int
    owner: str
    title: str
    done: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def mark_done(self) -> None:
        """Flip the task to done; idempotent."""
        self.done = True
