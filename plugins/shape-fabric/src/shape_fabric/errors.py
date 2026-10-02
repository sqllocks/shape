"""Errors and results shared by every writer of this plugin.

A writer raises :class:`WriteError` at the first failure, after rolling back what it can. The
error carries ``result``: what had been written completely before the failure (a table that
failed half way is never counted). Nothing is reported as written unless the destination
accepted it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from shape.errors import ShapeError


@dataclass(slots=True)
class WriteResult:
    """What a writer wrote: rows per table, in the order the tables were written."""

    destination: str
    per_table: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    @property
    def tables_written(self) -> int:
        return len(self.per_table)

    @property
    def rows_written(self) -> int:
        return sum(self.per_table.values())

    def summary(self) -> str:
        lines = [
            f"{self.destination}: {self.tables_written} tables, {self.rows_written:,} rows "
            f"in {self.elapsed_seconds:.1f}s"
        ]
        lines += [f"  {name:<30} {rows:>12,}" for name, rows in self.per_table.items()]
        return "\n".join(lines)


class WriteError(ShapeError):
    """A write failed. ``result`` holds the tables that were written completely before it."""

    def __init__(self, message: str, result: WriteResult | None = None) -> None:
        super().__init__(message)
        self.result = result


class AuthError(ShapeError):
    """No usable credential, or the destination refused the one given."""
