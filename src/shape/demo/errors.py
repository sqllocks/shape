"""Errors of ``shape demo``: bad input is a :class:`DemoError` (the command exits 2)."""

from __future__ import annotations

from shape.errors import ShapeError


class DemoError(ShapeError, ValueError):
    """The scenario, session, connection profile or option cannot be used."""


class SessionNotFoundError(DemoError, LookupError):
    """No saved demo session has this id."""


class ConnectionNotFoundError(DemoError, LookupError):
    """No connection profile has this name."""


def is_expected(exc: BaseException) -> bool:
    """Whether ``exc`` is a failure a run reports as a message (bad input, a destination that
    refused something, a missing plugin) rather than a bug that deserves a traceback."""
    from shape.scale.sinks.base import SinkError

    return isinstance(exc, (ShapeError, ValueError, ImportError, OSError, SinkError))
