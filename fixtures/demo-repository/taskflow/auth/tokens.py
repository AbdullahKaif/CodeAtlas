"""Session token encoding."""
import base64
import hashlib
import hmac
import time

from taskflow.config import SECRET_KEY, TOKEN_TTL_SECONDS


def sign(payload: str) -> str:
    """HMAC the payload with the application key."""
    digest = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return digest


def encode_token(username: str) -> str:
    """Produce an opaque token carrying the username and an expiry."""
    expires = int(time.time()) + TOKEN_TTL_SECONDS
    payload = f"{username}:{expires}"
    raw = f"{payload}:{sign(payload)}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_token(token: str) -> str | None:
    """Return the username for a valid, unexpired token, else None."""
    try:
        raw = base64.urlsafe_b64decode(token.encode()).decode()
        username, expires, signature = raw.rsplit(":", 2)
    except (ValueError, UnicodeDecodeError):
        return None
    payload = f"{username}:{expires}"
    if not hmac.compare_digest(signature, sign(payload)):
        return None
    if int(expires) < time.time():
        return None
    return username
