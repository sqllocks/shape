"""Errors and results shared by every writer of this plugin.

A writer raises :class:`WriteError` at the first failure, after rolling back what it can. The
error carries ``result``: what had been written completely before the failure (a table that
failed half way is never counted). Nothing is reported as written unless the destination
accepted it.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from typing import Any

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
    """A write failed. ``result`` holds the tables that were written completely before it;
    ``rows_committed`` is how many rows of the failed table a ``commit_rows`` write had already
    committed (0 when the table was rolled back completely)."""

    def __init__(
        self, message: str, result: WriteResult | None = None, rows_committed: int = 0
    ) -> None:
        super().__init__(message)
        self.result = result
        self.rows_committed = rows_committed


class AuthError(ShapeError):
    """No usable credential, or the destination refused the one given."""


class ConstraintError(WriteError):
    """Rows were loaded with constraints disabled (``constraints=disable``) and some constraint
    does not hold: the run ends with exit code 1 (a data verdict, not bad input), and the message
    names each table and constraint and says it was left disabled."""

    exit_code = 1


class MissingPluginError(ImportError):
    """A part of this plugin needs one of its optional plugins (an extra), which is not
    installed. The plugin itself loads without them (#310)."""


#: the optional plugins: module -> (distribution, extra of this plugin, what needs it)
OPTIONAL_PLUGINS = {
    "shape_eventhubs": (
        "sqllocks-shape-eventhubs",
        "eventhubs",
        "the Eventstream emitter and writer",
    ),
    "shape_sqlserver": (
        "sqllocks-shape-sqlserver",
        "sqlserver",
        "the SQL database, Warehouse, Synapse and SQL Server targets",
    ),
}


def missing_plugin(module: str) -> MissingPluginError:
    """The error for the optional plugin of ``module``: what needs it and how to install it."""
    top = module.split(".")[0]
    dist, extra, what = OPTIONAL_PLUGINS[top]
    return MissingPluginError(
        f"{what} need the {dist} plugin, which is not installed: pip install "
        f"'sqllocks-shape-fabric[{extra}]' (from the release's wheels: add --find-links with "
        "their folder, see docs/INSTALL.md)",
        name=top,
    )


def optional_plugin(module: str) -> Any:
    """``module`` of an optional plugin, imported now; :class:`MissingPluginError` (an
    ``ImportError`` naming the extra to install) when that plugin is not installed. Any other
    import failure is raised as it is."""
    top = module.split(".")[0]
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        if exc.name is not None and (exc.name == top or exc.name.startswith(f"{top}.")):
            raise missing_plugin(module) from exc
        raise
