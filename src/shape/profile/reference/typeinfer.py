"""Type inference confidence and the declared-versus-inferred comparison (W2-07).

Every column of a profile carries ``type_inference``: the profile's type, where the type came from
(``declared`` by a typed source, ``inferred`` from text, an ``option`` the caller gave, or the
``identifier_rule``), how well the values fit it, and the share of values that parse as each
candidate type. :func:`build` makes the entry while the column is profiled; :func:`report`
(``shape.types_report``) turns the entries of a stored profile into findings.

Parse shares are taken over the distinct values weighted by their counts for text, and over the
values themselves for numbers and timestamps, so they are exact.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.io.identifiers import DigitStats, judge, suspect

from ..sampling import TYPE_SHARES_ROWS

TYPES = ("integer", "float", "boolean", "date", "datetime")
SOURCES = ("declared", "inferred", "option", "identifier_rule")
MIN_CONFIDENCE = 0.99  # ``shape types`` reports an inferred type below this
CANDIDATE_SHARE = 0.5  # a text column this much one narrower type has that type as a candidate

# text that parses as a number (the reader's rule, ``readers._NUMBER``), and the cheap prefilter
_NUMBER = (
    r"^\s*[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
    r"|[iI][nN][fF](?:[iI][nN][iI][tT][yY])?|[nN][aA][nN])\s*$"
)
# the first characters a number or a date can have (after any white space): a superset test that
# needs no regular expression
_FIRST_CHARS = list(" \t\r\n0123456789.+-")
_FIRST_WORD_CHARS = list("iInN")
_ISO_DATE = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
_ISO_DATETIME = (
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]+)?)?"
    r"(?:Z|[+-][0-9]{2}(?::?[0-9]{2})?)?)?$"
)
_BOOL_WORDS = ("true", "false", "0", "1", "yes", "no")


def _share(part: float, total: float) -> float:
    return round(part / total, 6) if total else 0.0


def _none_shares() -> dict[str, float | None]:
    return dict.fromkeys(TYPES)


def _text_shares(
    values: Any, counts: np.ndarray[Any, Any] | None
) -> tuple[dict[str, float | None], bool]:
    """``(parse shares, sampled)`` of a text column from its distinct values and their counts
    (``None`` counts: every value counts once). Exact up to ``TYPE_SHARES_ROWS`` distinct values;
    beyond that, evenly spread distinct values stand in for all of them, each weighted by its count
    (the same rule as the pattern rates), and ``sampled`` is true."""
    values = pa.array(values) if not isinstance(values, (pa.Array, pa.ChunkedArray)) else values
    if isinstance(values, pa.ChunkedArray):
        values = values.combine_chunks()
    if not pa.types.is_string(values.type):
        values = pc.cast(values, pa.string())
    w = np.ones(len(values), dtype=np.float64) if counts is None else np.asarray(counts, np.float64)
    sampled = len(values) > TYPE_SHARES_ROWS
    if sampled:
        pick = np.linspace(0, len(values) - 1, TYPE_SHARES_ROWS).astype(np.int64)
        values, w = values.take(pa.array(pick)), w[pick]
    total = float(w.sum())
    if not total:
        return _none_shares(), sampled

    # booleans: the six words in any case, so a value of at most five characters
    shares: dict[str, float | None] = {}
    lengths = pc.utf8_length(values)
    shortest = pc.min(lengths).as_py()
    boolean = 0.0
    if shortest is not None and shortest <= 5:
        short = np.flatnonzero(
            np.asarray(pc.less_equal(lengths, 5).to_numpy(zero_copy_only=False), dtype=bool)
        )
        lowered = pc.utf8_lower(values.take(pa.array(short)))
        hit = np.asarray(
            pc.is_in(lowered, value_set=pa.array(_BOOL_WORDS)).to_numpy(zero_copy_only=False),
            dtype=bool,
        )
        boolean = float(w[short][hit].sum())
    shares["boolean"] = _share(boolean, total)
    # numbers and dates share a first character test; only those values are looked at further
    first = pc.utf8_slice_codeunits(values, 0, 1)
    cand = pc.or_(
        pc.is_in(first, value_set=pa.array(_FIRST_CHARS)),
        # ``inf``, ``infinity`` and ``nan`` are short: a long value that starts with i or n is not
        pc.and_(pc.is_in(first, value_set=pa.array(_FIRST_WORD_CHARS)), pc.less_equal(lengths, 11)),
    )
    cand_idx = np.flatnonzero(np.asarray(cand.to_numpy(zero_copy_only=False), dtype=bool))
    numbers = dates = datetimes = ints = 0.0
    if len(cand_idx):
        sub = values.take(pa.array(cand_idx))
        sw = w[cand_idx]
        is_num = np.asarray(
            pc.match_substring_regex(sub, _NUMBER).fill_null(False).to_numpy(zero_copy_only=False),
            dtype=bool,
        )
        numbers = float(sw[is_num].sum())
        if is_num.any():
            nums = sub.filter(pa.array(is_num))
            vals = pc.cast(pc.utf8_trim_whitespace(nums), pa.float64()).to_numpy(
                zero_copy_only=False
            )
            whole = np.isfinite(vals) & (vals == np.floor(vals))
            ints = float(sw[is_num][whole].sum())
        iso = np.asarray(
            pc.match_substring_regex(sub, _ISO_DATETIME)
            .fill_null(False)
            .to_numpy(zero_copy_only=False),
            dtype=bool,
        )
        if iso.any():
            cands = sub.filter(pa.array(iso))
            ok = _valid_iso(cands)
            datetimes = float(sw[iso][ok].sum())
            only_date = np.asarray(
                pc.match_substring_regex(cands, _ISO_DATE)
                .fill_null(False)
                .to_numpy(zero_copy_only=False),
                dtype=bool,
            )
            dates = float(sw[iso][ok & only_date].sum())
    shares["integer"] = _share(ints, total)
    shares["float"] = _share(numbers, total)
    shares["date"] = _share(dates, total)
    shares["datetime"] = _share(datetimes, total)
    return {t: shares[t] for t in TYPES}, sampled


_ISO_PARTS = (
    r"^(?P<y>[0-9]{4})-(?P<m>[0-9]{2})-(?P<d>[0-9]{2})"
    r"(?:[T ](?P<h>[0-9]{2}):(?P<mi>[0-9]{2})(?::(?P<s>[0-9]{2}))?)?"
)
_DAYS = np.array([31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])


def _valid_iso(values: Any) -> np.ndarray[Any, Any]:
    """Which ISO-looking values are real dates or datetimes (month 13 and February 30 are not).
    The parts are read with one regular expression and checked with numpy, so it costs the same
    for any number of invalid values; a zone suffix is not looked at (it does not change whether the
    date and time are real)."""
    parts = pc.extract_regex(values, _ISO_PARTS)

    def field(name: str) -> np.ndarray[Any, Any]:
        text = parts.field(name)  # a part the value does not have is the empty string
        col = pc.cast(pc.if_else(pc.equal(text, ""), pa.scalar("0"), text), pa.int32())
        return np.asarray(col.fill_null(0).to_numpy(zero_copy_only=False), dtype=np.int64)

    year, month, day = field("y"), field("m"), field("d")
    hour, minute, second = field("h"), field("mi"), field("s")
    leap = (year % 4 == 0) & ((year % 100 != 0) | (year % 400 == 0))
    in_month = np.where(
        (month >= 1) & (month <= 12), _DAYS[np.clip(month - 1, 0, 11)] + ((month == 2) & leap), 0
    )
    return np.asarray(
        (year >= 1)
        & (month >= 1)
        & (month <= 12)
        & (day >= 1)
        & (day <= in_month)
        & (hour <= 23)
        & (minute <= 59)
        & (second <= 59),
        dtype=bool,
    )


def _non_null(col: Any) -> Any:
    arr = col.arr
    if isinstance(arr, pa.ChunkedArray):
        arr = arr.combine_chunks() if arr.num_chunks != 1 else arr.chunk(0)
    return arr.drop_null()


def _numeric_shares(col: Any, prof: Any, n: int) -> dict[str, float | None]:
    """Parse shares of a typed numeric, boolean or timestamp column."""
    kind, dtype = col.kind, prof.dtype
    s = _none_shares()
    zero = dict.fromkeys(TYPES, 0.0)
    if kind in ("bool", "objbool"):
        return {**zero, "boolean": 1.0}
    if kind in ("int", "uint64", "objint", "float"):
        s = {**zero, "float": 1.0}
        s["integer"] = 1.0 if dtype == "integer" or kind != "float" else _whole_share(col, n)
        s["boolean"] = _zero_one_share(col, prof, n)
        return s
    if kind == "dt64":
        s = {**zero, "datetime": 1.0}
        s["date"] = 1.0 if dtype == "date" else _midnight_share(col, n)
        return s
    if kind == "objdate":
        return {**zero, "date": 1.0, "datetime": 1.0}
    if kind == "objdec":
        arr = _non_null(col)
        scale = getattr(arr.type, "scale", None)
        s = {**zero, "float": 1.0}
        if scale == 0:
            s["integer"] = 1.0
        else:
            f = pc.cast(arr, pa.float64(), safe=False).to_numpy(zero_copy_only=False)
            s["integer"] = _share(float((np.isfinite(f) & (f == np.floor(f))).sum()), n)
        return s
    return s


def _numbers(col: Any) -> np.ndarray[Any, Any]:
    """The non-null values of a numeric column as a numpy array (no copy for one chunk)."""
    arr = col.arr
    if arr.null_count:
        arr = arr.drop_null()
    if col.kind in ("int", "float"):
        return np.asarray(arr.to_numpy())
    return np.asarray(pc.cast(arr, pa.float64(), safe=False).to_numpy(zero_copy_only=False))


def _whole_share(col: Any, n: int) -> float:
    a = _numbers(col)
    return _share(float((np.isfinite(a) & (a == np.floor(a))).sum()), n)


def _zero_one_share(col: Any, prof: Any, n: int) -> float:
    lo, hi = _plain(prof.min_value), _plain(prof.max_value)
    if lo is not None and hi is not None:
        if hi < 0 or lo > 1:
            return 0.0
        if prof.dtype == "integer" and lo >= 0 and hi <= 1:
            return 1.0
    a = _numbers(col)
    return _share(float(((a == 0) | (a == 1)).sum()), n)


def _midnight_share(col: Any, n: int) -> float:
    arr = _non_null(col)
    per_day = {"s": 86_400, "ms": 86_400_000, "us": 86_400_000_000, "ns": 86_400_000_000_000}[
        arr.type.unit
    ]
    ints = pc.cast(arr, pa.int64()).to_numpy(zero_copy_only=False)
    return _share(float((ints % per_day == 0).sum()), n)


def _plain(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)) and math.isfinite(v):
        return float(v)
    return None


# the order in which a text column is read as the narrowest type every value fits (the profiler's
# own: booleans first, then numbers, then dates)
_NARROWEST = ("boolean", "integer", "float", "date", "datetime")


def narrowest(shares: dict[str, float | None]) -> str | None:
    """The narrowest type every value parses as (a share of 1), or ``None``."""
    for t in _NARROWEST:
        if shares.get(t) == 1.0:
            return t
    return None


def _identifier(col: Any, prof: Any) -> str | None:
    """The identifier rule's reason for an integer column: the reader's, else judged here from
    the column's range (all digits of one width, from the minimum and maximum)."""
    if col.identifier:
        return str(col.identifier)
    if prof.dtype != "integer" or col.type_source == "option":
        return None
    lo, hi = _plain(prof.min_value), _plain(prof.max_value)
    if lo is None or hi is None:
        return None
    stats = DigitStats(
        values=1,
        leading_zero=False,
        all_digits=lo >= 0,
        min_width=len(str(int(abs(lo)))),
        max_width=len(str(int(abs(hi)))),
    )
    return judge(prof.name, stats) or suspect(prof.name, stats)


def build(
    col: Any,
    prof: Any,
    rows: int,
    uniques: Any = None,
    counts: np.ndarray[Any, Any] | None = None,
) -> dict[str, Any]:
    """The ``type_inference`` entry of a column of ``rows`` profiled rows.

    ``col`` is the reader's column (its ``type_source``, ``declared`` and ``identifier``), ``prof``
    the column's profile, ``uniques`` and ``counts`` its distinct text values and their counts
    (for a column read as text)."""
    n = rows - prof.null_count
    textual = col.kind in ("str", "cat") and uniques is not None
    if n <= 0:
        shares: dict[str, float | None] = _none_shares()
    elif textual:
        shares, sampled = _text_shares(uniques, counts)
        if sampled:
            prof.samples.append("type_inference")
    elif col.kind in ("str", "cat", "objmix", "objtime", "objbin", "objdur", "nullobj"):
        shares = _none_shares()
    else:
        shares = _numeric_shares(col, prof, n)
    dtype = prof.dtype
    source = col.type_source
    if textual and n > 0 and dtype in TYPES:
        # the profiler retyped the text because every value parses as ``dtype`` (by its own,
        # wider, rules for dates and numbers): the shares of that type, and of the types it
        # contains, are whole
        shares = {**shares, dtype: 1.0, **({"float": 1.0} if dtype == "integer" else {})}
        if dtype == "datetime" and shares["date"] is None:
            shares["date"] = 0.0
    candidate: str | None = None
    confidence: float | None
    if n <= 0:
        confidence = None
    elif dtype != "string" or source in ("option", "identifier_rule") or not textual:
        confidence = 1.0  # every value is of the type: a typed column holds its type, the
        # profiler retypes text only when every value parses
    else:
        confidence = 1.0
        best = max(
            (shares[t] or 0.0, -i, t)
            for i, t in enumerate(("integer", "float", "date", "datetime", "boolean"))
        )
        if source == "inferred" and CANDIDATE_SHARE <= best[0] < 1.0:
            candidate, confidence = best[2], best[0]
    out: dict[str, Any] = {
        "type": dtype,
        "source": source,
        "confidence": confidence,
        "parse_shares": shares,
        "identifier": _identifier(col, prof),
    }
    if source == "declared":
        out["declared"] = col.declared
        out["inferred"] = _inferred(col, prof, shares, n)
    if candidate is not None:
        out["candidate"] = candidate
    return out


def _inferred(col: Any, prof: Any, shares: dict[str, float | None], n: int) -> str | None:
    """The narrowest type every value of a declared column parses as (``None`` for no values)."""
    if n <= 0:
        return None
    kind = col.kind
    if kind in ("str", "cat"):
        return narrowest(shares) or "string"
    if kind == "float":
        return "integer" if shares.get("integer") == 1.0 else "float"
    if kind == "dt64":
        return "date" if shares.get("date") == 1.0 else "datetime"
    if kind in ("int", "uint64", "objint"):
        return "integer"
    if kind == "objdec":
        return "integer" if shares.get("integer") == 1.0 else "float"
    if kind in ("bool", "objbool"):
        return "boolean"
    if kind == "objdate":
        return "date"
    return str(col.declared or prof.dtype)
