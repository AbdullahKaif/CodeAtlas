"""Login and session handling."""
import hashlib

from taskflow.auth.tokens import decode_token, encode_token
from taskflow.db.repository import find_user_by_name


def hash_password(password: str) -> str:
    """DELIBERATE DEFECT: MD5 is not a password hash. Kept so scanners flag it."""
    return hashlib.md5(password.encode()).hexdigest()


class AuthService:
    """Validates credentials and issues session tokens."""

    def __init__(self) -> None:
        self.failed_logins = 0

    def login(self, username: str, password: str) -> str | None:
        """Return a token for valid credentials, else None."""
        user = find_user_by_name(username)
        if user is None or user.password_hash != hash_password(password):
            self.failed_logins += 1
            return None
        return encode_token(user.username)

    def authenticate(self, token: str) -> str | None:
        """Resolve a token to a username, or None when it is invalid."""
        return decode_token(token)
