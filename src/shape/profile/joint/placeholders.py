"""Placeholder and sentinel detection (#47).

A placeholder is a value that is valid for its column but stands for "no real value": ``00000``,
``99999``, ``1900-01-01``, ``9999-12-31``, ``-1``, ``N/A``, ``UNKNOWN``, ``TEST``, a string of one
repeated digit. It is found from what the column profile already holds (the share of each of the
most frequent values), so it costs nothing beyond that profile and never reads the data again.

Two kinds of evidence, both reported with the value, its share of the rows and the numbers behind
the call:

* **listed**: the value is one of the well-known placeholders (or a repeated digit or letter
  string) and is common enough to matter (``MIN_LISTED_SHARE`` of the non-null values);
* **spike**: in a column of many distinct values (its cardinality ratio is at least
  ``SPIKE_MIN_RATIO``), one value is at least ``SPIKE_MIN_SHARE`` of the non-null values and at
  least ``SPIKE_FACTOR`` times as frequent as the next one, which a real value of such a column
  rarely is. A dominant value of a low-cardinality column (a status that is 70% "active") is not
  flagged.
"""

from __future__ import annotations

import re
from typing import Any

MIN_LISTED_SHARE = 0.002  # a listed placeholder below 0.2% of the non-null values is noise
SPIKE_MIN_SHARE = 0.02
SPIKE_FACTOR = 5.0
SPIKE_MIN_RATIO = 0.2  # cardinality / non-null values: the column is mostly distinct values
SPIKE_MIN_CARDINALITY = 20
MAX_PER_COLUMN = 5

_NUMBERS = frozenset(
    {
        "-1",
        "-9",
        "-99",
        "-999",
        "-9999",
        "-99999",
        "-999999",
        "99",
        "999",
        "9999",
        "99999",
        "999999",
        "9999999",
        "99999999",
        "999999999",
        "-1.0",
        "-999.0",
        "-9999.0",
        "99999.0",
        "123456789",
        "12345",
        "1234",
        "123456",
        "12345678",
    }
)
_DATES = frozenset(
    {
        "1900-01-01",
        "1899-12-30",
        "1899-12-31",
        "1753-01-01",
        "1970-01-01",
        "1980-01-01",
        "2000-01-01",
        "2099-12-31",
        "2999-12-31",
        "9999-12-31",
        "0001-01-01",
        "0000-00-00",
    }
)
_LITERALS = frozenset(
    {
        "n/a",
        "na",
        "n.a.",
        "n.a",
        "n\\a",
        "none",
        "null",
        "nil",
        "nan",
        "unknown",
        "unk",
        "undefined",
        "unspecified",
        "missing",
        "tbd",
        "tba",
        "test",
        "testing",
        "dummy",
        "sample",
        "placeholder",
        "default",
        "blank",
        "empty",
        "not available",
        "not applicable",
        "no data",
        "no value",
        "xxx",
        "xxxx",
        "-",
        "--",
        "---",
        "?",
        "??",
        "???",
        ".",
        "..",
        "asdf",
        "qwerty",
        "foo",
        "foobar",
        "lorem ipsum",
        "(blank)",
        "(none)",
        "(null)",
        "<null>",
        "[null]",
    }
)
_REPEATED_DIGIT = re.compile(r"^-?(\d)\1{2,}(\.0+)?$")
_REPEATED_CHAR = re.compile(r"^([a-zA-Z])\1{2,}$")
_ZERO_DATE = re.compile(r"^0{1,4}-0{1,2}-0{1,2}( 00:00:00)?$")
_ZERO_TIME = re.compile(r"^(0001-01-01|1900-01-01|1970-01-01|9999-12-31|1899-12-30)[ T]")


def _kind(value: str) -> str | None:
    """Why ``value`` is a well-known placeholder, or None."""
    low = value.strip().lower()
    if low in _LITERALS:
        return "text_literal"
    if len(low) >= 3 and low.strip("0") == "":
        return "zeros"
    if low in _NUMBERS:
        return "numeric_sentinel"
    if low in _DATES or _ZERO_DATE.match(low) or _ZERO_TIME.match(low):
        return "date_sentinel"
    if _REPEATED_DIGIT.match(low):
        return "repeated_digits"
    if _REPEATED_CHAR.match(low):
        return "repeated_letters"
    return None


def detect_placeholders(
    counts: dict[str, float] | None,
    *,
    null_rate: float | None,
    cardinality: int,
    row_count: int,
) -> list[dict[str, Any]]:
    """The placeholder values of one column.

    ``counts`` maps a value (as its text) to its share of the non-null values, in descending
    order (the profile's ``value_counts_ext``). Each result is ``{"value", "kind", "share",
    "count", "share_of_non_null", "next_share", "evidence"}`` where ``share`` is of all rows."""
    if not counts or row_count <= 0:
        return []
    nn_rows = row_count * (1.0 - (null_rate or 0.0))
    if nn_rows <= 0:
        return []
    items = list(counts.items())
    ratio = cardinality / nn_rows if nn_rows else 0.0
    out: list[dict[str, Any]] = []
    for rank, (value, share) in enumerate(items):
        if share <= 0:
            continue
        nxt = items[rank + 1][1] if rank + 1 < len(items) else 0.0
        sibling = (items[1][1] if rank == 0 else items[0][1]) if len(items) > 1 else 0.0
        listed = _kind(str(value))
        spike = (
            listed is None
            and rank == 0
            and cardinality >= SPIKE_MIN_CARDINALITY
            and ratio >= SPIKE_MIN_RATIO
            and share >= SPIKE_MIN_SHARE
            and share >= SPIKE_FACTOR * max(nxt, 1e-12)
        )
        if listed is not None and share >= MIN_LISTED_SHARE:
            kind = listed
            reason = (
                f"{value!r} is a well-known placeholder ({listed.replace('_', ' ')}) and is "
                f"{share:.1%} of the non-null values"
            )
        elif spike:
            kind = "spike"
            reason = (
                f"{value!r} is {share:.1%} of the non-null values of a column with "
                f"{cardinality} distinct values, {share / max(nxt, 1e-12):.0f}x as frequent as the "
                "next most frequent value"
            )
        else:
            continue
        out.append(
            {
                "value": str(value),
                "kind": kind,
                "share": round(share * nn_rows / row_count, 6),
                "count": int(round(share * nn_rows)),
                "share_of_non_null": round(float(share), 6),
                "next_share": round(float(sibling), 6),
                "evidence": reason,
            }
        )
        if len(out) >= MAX_PER_COLUMN:
            break
    return out
