from typing import Optional


class BaseError(Exception):
    """An exception that should be caught and handled by the application."""


class ApplicationError(BaseError):
    """Base class for exceptions in this module."""

    message: str
    details: Optional[str]

    def __init__(self, message: str, details: Optional[str] = None):
        self.message = message
        self.details = details
        super().__init__(message)

    def render(self):
        _repr = f"[danger]{self.message}[/]\n"
        if self.details:
            _repr += f"[details]{self.details}[/]\n"
        return _repr
