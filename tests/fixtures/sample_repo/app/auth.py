"""Authentication logic for the sample application."""
from app.base import BaseService
from app.database import find_user


class AuthService(BaseService):
    """Handles user authentication."""

    def __init__(self) -> None:
        super().__init__("auth")
        self.failed_attempts = 0

    def login(self, username: str, password: str) -> bool:
        """Validate credentials against the user store."""
        user = find_user(username)
        if user is None:
            self.failed_attempts += 1
            return False
        return user.get("password") == password

    def logout(self, username: str) -> None:
        """Log the user out."""
        print(f"{username} logged out")
