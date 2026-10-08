"""Environment parity (W3-13): does environment B still look like environment A?

``shape parity A B`` compares two sides, each data or a profile, by shape only: the tables,
columns, types, keys and relationships, null rates and distributions, and the sizes of the
tables. See ``docs/PARITY.md``.
"""

from __future__ import annotations

from .checks import (
    CATEGORIES,
    DEFAULT_ROW_TOLERANCE,
    FAIL,
    NOT_MEASURED,
    PASS,
    Check,
    compare,
)
from .report import FORMAT, VERSION, build_report, render_text
from .sides import ParityInputError, Side, load_side

__all__ = [
    "CATEGORIES",
    "DEFAULT_ROW_TOLERANCE",
    "FAIL",
    "FORMAT",
    "NOT_MEASURED",
    "PASS",
    "VERSION",
    "Check",
    "ParityInputError",
    "Side",
    "build_report",
    "compare",
    "load_side",
    "render_text",
]
