"""Base service class used across the sample application."""


class BaseService:
    """Common behaviour for all services."""

    def __init__(self, name: str) -> None:
        self.name = name

    def describe(self) -> str:
        """Return a human readable description of the service."""
        return f"Service: {self.name}"
