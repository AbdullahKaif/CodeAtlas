"""Authentication tests."""
from taskflow.auth.service import AuthService, hash_password
from taskflow.auth.tokens import decode_token, encode_token


def test_login_unknown_user_fails(auth_service: AuthService) -> None:
    assert auth_service.login("nobody", "password") is None
    assert auth_service.failed_logins == 1


def test_token_round_trip() -> None:
    token = encode_token("alice")
    assert decode_token(token) == "alice"


def test_tampered_token_is_rejected() -> None:
    token = encode_token("alice")
    assert decode_token(token[:-2] + "zz") is None


def test_hash_is_deterministic() -> None:
    assert hash_password("secret") == hash_password("secret")
