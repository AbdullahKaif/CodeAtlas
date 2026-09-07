"""A tiny route registry, so handlers can be declared with a decorator."""
from typing import Callable


class Router:
    """Collects handlers by method and path."""

    def __init__(self) -> None:
        self.routes: dict[tuple[str, str], Callable] = {}

    def get(self, path: str) -> Callable:
        """Register a GET handler."""
        return self._register("GET", path)

    def post(self, path: str) -> Callable:
        """Register a POST handler."""
        return self._register("POST", path)

    def _register(self, method: str, path: str) -> Callable:
        def decorator(handler: Callable) -> Callable:
            self.routes[(method, path)] = handler
            return handler

        return decorator

    def dispatch(self, method: str, path: str, **kwargs):
        """Call the handler registered for a method and path."""
        handler = self.routes.get((method, path))
        if handler is None:
            raise LookupError(f"No route for {method} {path}")
        return handler(**kwargs)


router = Router()
