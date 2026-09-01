"""Tests for the sample app (fixture content only - not executed by CodeAtlas tests)."""
from app.auth import AuthService


def test_login_unknown_user_fails():
    service = AuthService()
    assert service.login("nobody", "password") is False
