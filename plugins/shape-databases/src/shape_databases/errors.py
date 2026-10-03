"""Errors of the database sinks.

Nothing here ever carries a password, token or connection secret: messages are built from
names and counts, and anything that came from a database driver is scrubbed first (see
:func:`shape_databases._auth.scrub`).
"""

from __future__ import annotations

from shape.errors import ShapeError


class WriteError(ShapeError):
    """A write failed. ``rows_committed`` is how many rows a ``commit_rows`` write had already
    committed (and so are visible to readers) when it stopped; it is 0 for a write that rolled
    back completely."""

    def __init__(self, message: str, rows_committed: int = 0) -> None:
        super().__init__(message)
        self.rows_committed = rows_committed


class CredentialError(ShapeError):
    """No usable password or credential. The message names the source, never the value."""
