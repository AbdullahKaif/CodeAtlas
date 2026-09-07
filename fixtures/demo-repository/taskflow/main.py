"""Entry point: wires the routes and serves requests from the command line."""
import sys

from taskflow.api import routes  # noqa: F401  - registers the handlers
from taskflow.api.router import router


def handle(method: str, path: str, **kwargs) -> dict:
    """Dispatch one request through the router."""
    return router.dispatch(method, path, **kwargs)


def main(argv: list[str] | None = None) -> int:
    """Log in with the arguments given and print the caller's tasks."""
    args = argv if argv is not None else sys.argv[1:]
    if len(args) < 2:
        print("usage: python -m taskflow.main <username> <password>")
        return 2
    session = handle("POST", "/login", username=args[0], password=args[1])
    if "token" not in session:
        print(session["error"])
        return 1
    print(handle("GET", "/tasks", token=session["token"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
