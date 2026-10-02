"""The landing-layout options shared by ``generate``, ``continue`` and ``chaos``.

``--path-template``, ``--batch-date`` and ``--table-format`` (see :mod:`shape.io.landing`). Nothing
heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
from typing import Any


def add_landing_arguments(parser: Any, *, default_template: str | None = None) -> None:
    """Add the landing option group to ``parser``."""
    group = parser.add_argument_group("landing layout")
    group.add_argument(
        "--path-template",
        metavar="TEMPLATE",
        help="where each table's file goes under -o, e.g. "
        "'{table}/ingest_date={date}/{table}_{yyyymmdd}.{ext}' (tokens: {table} {ext} {date} "
        "{yyyymmdd} {yyyy} {mm} {dd})"
        + (f"; default: {default_template}" if default_template else ""),
    )
    group.add_argument(
        "--batch-date",
        metavar="YYYY-MM-DD",
        help="the business date the date tokens take (never read from the clock)",
    )
    group.add_argument(
        "--table-format",
        metavar="TABLE=FORMAT",
        action="append",
        help="the format of one table (repeatable); the other tables use --format",
    )


def landing_requested(a: argparse.Namespace) -> bool:
    """True when any landing option was given."""
    return bool(
        getattr(a, "path_template", None)
        or getattr(a, "batch_date", None)
        or getattr(a, "table_format", None)
    )


def landing_options(a: argparse.Namespace, default_template: str) -> dict[str, Any]:
    """The keyword arguments of :func:`shape.generation.landing.write_landing` the options set."""
    from shape.io.landing import parse_table_formats

    return {
        "template": a.path_template or default_template,
        "batch_date": a.batch_date,
        "formats": parse_table_formats(a.table_format),
    }


__all__ = ["add_landing_arguments", "landing_options", "landing_requested"]
