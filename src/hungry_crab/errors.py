"""User-facing errors. The CLI prints the message (and hint) and maps the class to an exit code."""

from __future__ import annotations


class CrabError(Exception):
    """An error the CLI reports to the user."""

    exit_code = 1

    def __init__(self, message: str, *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint


class UsageError(CrabError):
    """The command line or a configuration value was used incorrectly."""

    exit_code = 2


class ToolMissingError(CrabError):
    """A required external tool (git, gh) is not available."""

    exit_code = 3


class ExternalCommandError(CrabError):
    """An external command (git, gh) failed."""

    exit_code = 4
