"""Entry point for the sample application."""
from app.auth import AuthService


def run() -> None:
    service = AuthService()
    if service.login("admin", "admin"):
        print("logged in")


if __name__ == "__main__":
    run()
