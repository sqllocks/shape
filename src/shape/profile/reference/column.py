"""Per-column profiling."""

from __future__ import annotations

import datetime as _dt
import re
import threading
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel
from shape.kernel.reference.exact import PCTS as _PCTS
from shape.kernel.reference.exact import all_whole
from shape.kernel.reference.exact import lerp as _lerp
from shape.kernel.reference.exact import linear_index as _linear_index
from shape.profile.fitting import detect_distribution as _kernel_detect_distribution

from . import dtparse
from .model import ColumnProfile, Timedelta, Timestamp
from .readers import _Col

# ---------------------------------------------------------------------------
# helpers mirroring pandas behaviour
# ---------------------------------------------------------------------------

_EMAIL_RE = r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
_PHONE_RE = r"^[\+]?[\d\s\-\(\)\.]{7,20}$"
_UUID_RE = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
_DATE_RE = r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$"
_SSN_RE = r"^\d{3}-\d{2}-\d{4}$"
_OCT = r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
_IP_V4_RE = rf"^{_OCT}\.{_OCT}\.{_OCT}\.{_OCT}$"
_IP_V6_RE = (
    r"^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$"
    r"|^(?:[0-9a-fA-F]{1,4}:){1,7}:$"
    r"|^:(?::[0-9a-fA-F]{1,4}){1,7}$"
    r"|^(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$"
    r"|^::(?:[fF]{4}:){0,1}\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$"
    r"|^::$"
)
_MAC_RE = r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$|^([0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}$"
_CURRENCY_CODE_RE = r"^[A-Z]{3}$"
_LANGUAGE_CODE_RE = r"^[a-z]{2}(-[A-Z]{2})?$"
_IBAN_RE = r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$"
_POSTAL_US_RE = r"^\d{5}(-\d{4})?$"


def _pandas_fullmatch_pattern(pat: str) -> str:
    """ArrowStringArrayMixin._str_fullmatch -> _str_match pattern rewriting (pandas 3.0)."""
    if (not pat.endswith("$") or pat.endswith("\\$")) and not pat.startswith("^"):
        pat = f"^({pat})$"
    elif not pat.endswith("$") or pat.endswith("\\$"):
        pat = f"^({pat[1:]})$"
    elif not pat.startswith("^"):
        pat = f"^({pat[0:-1]})$"
    if pat.startswith("^"):
        pat = pat[1:]
    return f"^({pat})"


_PATTERNS = {
    k: _pandas_fullmatch_pattern(v)
    for k, v in {
        "email": _EMAIL_RE,
        "uuid": _UUID_RE,
        "ssn": _SSN_RE,
        "mac": _MAC_RE,
        "ipv4": _IP_V4_RE,
        "ipv6": _IP_V6_RE,
        "iban": _IBAN_RE,
        "postal": _POSTAL_US_RE,
        "date": _DATE_RE,
        "phone": _PHONE_RE,
        "currency": _CURRENCY_CODE_RE,
        "language": _LANGUAGE_CODE_RE,
    }.items()
}


_PATTERN_SAMPLE_LOCK = threading.Lock()


def _pattern_sample(n: int) -> np.ndarray:
    """Cached draw (under a lock: concurrent columns of the same length draw it once)."""
    with _PATTERN_SAMPLE_LOCK:
        return _pattern_sample_cached(n)


@lru_cache(maxsize=64)
def _pattern_sample_cached(n: int) -> np.ndarray:
    """The 1000 row positions sampled for pattern detection (the same for every column of n rows:
    drawing them permutes all n positions, which costs more than the detection itself)."""
    idx: np.ndarray = np.random.RandomState(42).choice(n, size=1000, replace=False)
    idx.setflags(write=False)
    return idx


def detect_pattern(non_null: pa.Array, cardinality: int) -> str | None:
    """DataProfiler._detect_pattern on a non-null string array."""
    n = len(non_null)
    if n == 0:
        return None
    sample = non_null
    if n > 1000:
        sample = non_null.take(pa.array(_pattern_sample(n)))
    total = len(sample)
    thr = 0.9

    def rate(key: str) -> float:
        return float(pc.sum(pc.match_substring_regex(sample, _PATTERNS[key])).as_py() / total)

    if rate("email") >= thr:
        return "email"
    if rate("uuid") >= thr:
        return "uuid"
    if rate("ssn") >= thr:
        return "ssn"
    if rate("mac") >= thr:
        return "mac_address"
    v4, v6 = rate("ipv4"), rate("ipv6")
    if v4 >= thr or v6 >= thr:
        return "ip_address"
    if rate("iban") >= thr:
        return "iban"
    if rate("postal") >= thr:
        return "postal_code"
    if rate("date") >= thr:
        return "date"
    if rate("phone") >= thr:
        return "phone"
    if rate("currency") >= thr and cardinality <= 200:
        return "currency_code"
    if rate("language") >= thr and cardinality <= 200:
        return "language_code"
    return None


# Rates of personal-data patterns (#2). ``pattern`` is one label gated at 90% of a 1,000-row
# sample, so a column where 4% of the values are SSNs reports nothing. These rates are measured
# on every distinct value (weighted by its count), up to ``_RATE_MAX_DISTINCT`` of them; beyond
# that an evenly spaced sample of the distinct values stands in for all of them. Whole-value rates
# are taken for the personal-data families and for the detected ``pattern``; each family is first
# narrowed by a plain substring every match must hold, which is far cheaper than the regex.
_RATE_LABELS = {"mac": "mac_address", "postal": "postal_code", "currency": "currency_code"}
_RATE_LABELS |= {"language": "language_code", "ipv4": "ip_address", "ipv6": "ip_address"}
_RATE_LABELS |= {"ssn": "ssn", "email": "email", "iban": "iban"}
_RATE_FAMILIES = ("email", "ssn", "ipv4", "ipv6", "iban")
_NEEDS = {"email": "@", "ssn": "-", "ipv4": ".", "ipv6": ":"}
_CONTAINS_RE = {
    "ssn": r"(?:^|\D)\d{3}-\d{2}-\d{4}(?:\D|$)",
    "email": r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
    "credit_card": r"(?:^|\D)\d(?:[ -]?\d){12,18}(?:\D|$)",
}
_CONTAINS_NEEDS = {"ssn": "-", "email": "@"}
_CARD_RUN = re.compile(r"\d(?:[ -]?\d){12,18}")
_RATE_MAX_DISTINCT = 50_000


def _luhn(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


def _has_card(text: str) -> bool:
    for m in _CARD_RUN.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False


def _mask(uniq: pa.Array, pat: str, needs: str | None) -> np.ndarray:
    """Which of ``uniq`` match the regex ``pat``; ``needs`` is a substring every match contains."""
    if needs is None:
        hit = pc.match_substring_regex(uniq, pat).fill_null(False)
        return np.asarray(hit.to_numpy(zero_copy_only=False), dtype=bool)
    cand = np.asarray(
        pc.match_substring(uniq, needs).fill_null(False).to_numpy(zero_copy_only=False), dtype=bool
    )
    out = np.zeros(len(uniq), dtype=bool)
    if cand.any():
        hit = pc.match_substring_regex(uniq.filter(pa.array(cand)), pat).fill_null(False)
        out[np.flatnonzero(cand)] = np.asarray(hit.to_numpy(zero_copy_only=False), dtype=bool)
    return out


def pattern_rates(
    uniq: pa.Array, counts: np.ndarray, n_nn: int, detected: str | None = None
) -> tuple[dict[str, float], dict[str, float]]:
    """``(whole, contains)``: the share of the non-null values that are entirely a pattern of each
    personal-data family (and of the detected ``pattern``), and the share that contain an SSN,
    email address or card number. Zero rates are left out."""
    if n_nn == 0 or len(uniq) == 0:
        return {}, {}
    counts = np.asarray(counts, dtype=np.int64)
    if len(uniq) > _RATE_MAX_DISTINCT:
        pick = np.linspace(0, len(uniq) - 1, _RATE_MAX_DISTINCT).astype(np.int64)
        uniq = uniq.take(pa.array(pick))
        counts = counts[pick]
        n_nn = int(counts.sum())
    keys = list(_RATE_FAMILIES)
    for key, label in _RATE_LABELS.items():
        if label == detected and key not in keys:
            keys.append(key)
    whole: dict[str, float] = {}
    for key in keys:
        hit = int(counts[_mask(uniq, _PATTERNS[key], _NEEDS.get(key))].sum())
        if hit:
            label = _RATE_LABELS[key]
            whole[label] = whole.get(label, 0.0) + hit / n_nn
    contains: dict[str, float] = {}
    for key, pat in _CONTAINS_RE.items():
        mask = _mask(uniq, pat, _CONTAINS_NEEDS.get(key))
        if key == "credit_card" and mask.any():
            keep = np.array([_has_card(t) for t in uniq.filter(pa.array(mask)).to_pylist()])
            mask = mask.copy()
            mask[np.flatnonzero(mask)] = keep
        hit = int(counts[mask].sum())
        if hit:
            contains[key] = hit / n_nn
    return {k: round(v, 6) for k, v in whole.items()}, {k: round(v, 6) for k, v in contains.items()}


_FIXED_OFFSET = re.compile(r"([+-])(\d{2}):?(\d{2})")
_UNIT_TO_US = {"s": (1_000_000, 1), "ms": (1000, 1), "us": (1, 1), "ns": (1, 1000)}


def _tzinfo(tz: str) -> _dt.tzinfo:
    """The ``tzinfo`` of a column's zone. UTC and fixed offsets need no time-zone database (Windows
    has none); a named zone does (#21)."""
    if tz.upper() in ("UTC", "Z"):
        return _dt.UTC
    m = _FIXED_OFFSET.fullmatch(tz)
    if m:
        delta = _dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
        return _dt.timezone(-delta if m.group(1) == "-" else delta)
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
        raise ValueError(
            f"cannot read the time zone {tz!r}: this system has no time-zone database. "
            "Install the 'tzdata' package (pip install tzdata)."
        ) from exc


def _local_timestamp(aware: pa.Array, tz: str) -> pa.Array:
    """The wall-clock (zone-less) timestamps of a zoned column. Arrow's conversion is used for
    named zones (it needs the database); UTC and fixed offsets are plain integer arithmetic."""
    tzinfo = _tzinfo(tz)
    offset = _dt.datetime(1970, 1, 1, tzinfo=_dt.UTC).astimezone(tzinfo).utcoffset()
    if isinstance(tzinfo, _dt.timezone) and offset is not None:
        per_sec = {"s": 1, "ms": 1000, "us": 1_000_000, "ns": 1_000_000_000}[aware.type.unit]
        shift = int(offset.total_seconds()) * per_sec
        ints = pc.cast(aware, pa.int64())
        return pc.cast(pc.add(ints, pa.scalar(shift, pa.int64())), pa.timestamp(aware.type.unit))
    try:
        return pc.local_timestamp(aware)
    except pa.ArrowInvalid as exc:
        raise ValueError(
            f"cannot read the time zone {tz!r}: this system has no time-zone database. "
            "Install the 'tzdata' package (pip install tzdata)."
        ) from exc


def _aware_datetimes(aware: pa.Array, tz: str) -> list[_dt.datetime | None]:
    """The values of a zoned column as aware datetimes (nanoseconds cut to microseconds), built
    from the integers so that pyarrow never needs a time-zone database."""
    tzinfo = _tzinfo(tz)
    mul, div = _UNIT_TO_US[aware.type.unit]
    epoch = _dt.datetime(1970, 1, 1, tzinfo=_dt.UTC)
    ints = pc.cast(aware, pa.int64()).to_pylist()
    return [
        None
        if v is None
        else (epoch + _dt.timedelta(microseconds=v * mul // div)).astimezone(tzinfo)
        for v in ints
    ]


MAX_VALUE_CHARS = 256  # a stored text value (a value-count key, a minimum, a maximum) is cut here


def _capped(keys: list[str], shares: Any) -> dict[str, float]:
    """``dict(zip(keys, shares))`` with keys longer than ``MAX_VALUE_CHARS`` cut (and marked with
    an ellipsis), so a column of long documents cannot make the profile as big as the data (#37).
    Keys that become equal add their shares."""
    out: dict[str, float] = {}
    for k, v in zip(keys, shares, strict=True):
        if len(k) > MAX_VALUE_CHARS:
            k = k[:MAX_VALUE_CHARS] + "\u2026"
            out[k] = round(out.get(k, 0.0) + float(v), 6)
        else:
            out[k] = v
    return out


def _iso_strings(values: pa.Array, unit: str) -> list[str | None]:
    """Format each value as "YYYY-MM-DD[ HH:MM:SS]", like pc.strftime but without a tz database
    (pyarrow's strftime needs one even for naive timestamps, and Windows has none)."""
    arr = values.cast(pa.timestamp("s") if unit == "s" else pa.date32())
    np_vals = arr.to_numpy(zero_copy_only=False).astype(f"datetime64[{unit}]")
    out = np.datetime_as_string(np_vals, unit=cast(Any, unit)).tolist()
    valid = arr.is_valid().to_pylist()
    return [s.replace("T", " ") if ok else None for s, ok in zip(out, valid, strict=True)]


def _keys_py(values: pa.Array, kind: str) -> list[str]:
    """str(k) for the keys of pandas' value_counts index."""
    if kind in ("bool", "objbool"):
        return ["True" if v else "False" for v in values.to_pylist()]
    if kind in ("int", "uint64", "objint"):
        return [str(int(v)) for v in values.to_pylist()]
    if kind == "float":
        if values.null_count == 0:
            return cast(
                list[str], _pa(get_kernel().float_repr(pc.cast(values, pa.float64()))).to_pylist()
            )
        return [str(float(v)) for v in values.to_numpy(zero_copy_only=False).tolist()]
    if kind == "dt64":
        return (
            cast(list[str], _iso_strings(pc.cast(values, pa.timestamp("s")), "s"))
            if _no_subsecond(values)
            else [str(_to_timestamp(v)) for v in values.to_pylist()]
        )
    if kind == "objdate":
        return cast(list[str], _iso_strings(values, "D"))
    return cast(list[str], values.to_pylist())


def _no_subsecond(ts: pa.Array) -> bool:
    unit = ts.type.unit
    if unit == "s":
        return True
    mult = {"ms": 1000, "us": 1_000_000, "ns": 1_000_000_000}[unit]
    ints = pc.cast(ts, pa.int64()).to_numpy(zero_copy_only=False)
    return not np.any(ints % mult)


def _to_timestamp(v: _dt.datetime) -> Timestamp:
    return Timestamp(  # fold keeps the second 01:30 of a DST fall-back on its own offset (#225)
        v.year, v.month, v.day, v.hour, v.minute, v.second, v.microsecond, v.tzinfo, fold=v.fold
    )


def _round6(arr: np.ndarray) -> list[float]:
    """``round(v, 6)`` of each value, on the kernel."""
    out = get_kernel().round6(pa.array(np.ascontiguousarray(arr, dtype=np.float64)))
    return cast(list[float], _pa(out).to_pylist())


# ---------------------------------------------------------------------------
# datetime handling for strings / objects
# ---------------------------------------------------------------------------

_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ISO_DT = re.compile(r"^\d{4}-\d{2}-\d{2}([ T])\d{2}:\d{2}:\d{2}$")
_ISO_DT_FRAC = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}\.\d+$")


def _try(fn: Callable[..., Any], arr: Any) -> bool:
    """True if fn(arr) succeeds.  Probes the first element first so that a column that
    obviously does not parse fails fast (pandas also stops at the first failure)."""
    for a in (arr.slice(0, 1), arr) if len(arr) > 1 else (arr,):
        try:
            fn(a)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            return False
    return True


_PARSE_SLICE = 256


def _all_parse_datetime(uniques: pa.Array) -> bool:
    """Port of `pd.to_datetime(values, format="mixed")` succeeding on every value: what Arrow's
    ISO-8601 cast accepts, else pandas' own readers and dateutil (``dtparse.parse_mixed``)."""
    if _try(lambda a: pc.cast(a, pa.timestamp("ns")), uniques):
        return True
    # ordinary text fails on its first value: convert the distinct values a slice at a time, not
    # all of them (a column of 200k distinct strings would cost 10 ms before the first parse)
    for start in range(0, len(uniques), _PARSE_SLICE):
        for u in uniques.slice(start, _PARSE_SLICE).to_pylist():
            if dtparse.parse_mixed(u) is None:
                return False
    return True


def _first_is_iso(arr: Any) -> bool:
    """True when the first element is ISO-8601 text, the forms Arrow parses in bulk."""
    if len(arr) == 0:
        return False
    first = arr[0].as_py()
    return bool(_ISO_DATE.match(first) or _ISO_DT.match(first) or _ISO_DT_FRAC.match(first))


def _coerce_datetime_strings(arr: Any, keep_nulls: bool = False, uniq: Any = None) -> Any:
    """pd.to_datetime(series, errors="coerce"): the format is guessed from the first element
    and applied strictly (non-matching elements become NaT, dropped unless keep_nulls); with no
    guessable format every element is parsed on its own. ISO-8601 text, the common case, takes
    Arrow's vectorised parsers; everything else goes through ``dtparse`` once per distinct
    value."""
    if len(arr) == 0:
        return pa.array([], pa.timestamp("ns"))
    first = arr[0].as_py()
    fmt = None
    if _ISO_DATE.match(first):
        fmt = "%Y-%m-%d"
    elif m := _ISO_DT.match(first):
        fmt = f"%Y-%m-%d{m.group(1)}%H:%M:%S"
    elif _ISO_DT_FRAC.match(first):
        out = (
            pc.cast(arr, pa.timestamp("ns"), safe=False)
            if _try(lambda a: pc.cast(a, pa.timestamp("ns")), arr)
            else None
        )
        if out is None:
            return None if keep_nulls else pa.array([], pa.timestamp("ns"))
        return out if keep_nulls else pc.drop_null(out)
    else:
        return _coerce_with_dtparse(arr, keep_nulls)
    parsed = pc.strptime(arr, format=fmt, unit="s", error_is_null=True)
    has_time = fmt != "%Y-%m-%d"
    # the distinct values (when given and much fewer than the rows) say whether any rolled over
    if uniq is None or 4 * len(uniq) >= len(arr) or _rolls_over(uniq, fmt, has_time):
        parsed = _reject_rolled_over(arr, parsed, has_time)
    return parsed if keep_nulls else pc.drop_null(parsed)


_ISO_FIELDS = re.compile(r"^\d+-(?P<mo>\d+)-(?P<d>\d+)(?:[ T](?P<h>\d+):(?P<mi>\d+):(?P<s>\d+))?$")


def _rolled_over_chunk(text: Any, parsed: Any, has_time: bool) -> tuple[Any, Any]:
    """``(bad, unchecked)`` row positions of one chunk. A value can only roll over when its text
    says day 29 to 31 or second 60 or 61, so those two digits are read straight from the string
    buffers of fixed-width ISO text, and only rows that name such a day or second are compared
    with what ``parsed`` holds. Rows of another width are returned to be checked otherwise."""
    none = np.zeros(0, np.int64)
    if not (pa.types.is_string(text.type) or pa.types.is_large_string(text.type)):
        return none, np.flatnonzero(parsed.is_valid().to_numpy(zero_copy_only=False))
    n = len(text)
    bufs = text.buffers()
    off_type = np.int64 if pa.types.is_large_string(text.type) else np.int32
    offsets = np.frombuffer(bufs[1], off_type)[text.offset : text.offset + n + 1]
    if bufs[2] is None:
        return none, none
    data = np.frombuffer(bufs[2], np.uint8)
    valid = parsed.is_valid().to_numpy(zero_copy_only=False)
    fixed = np.diff(offsets) == (19 if has_time else 10)
    start = np.where(fixed, offsets[:-1], 0)

    def two(pos: int) -> Any:
        return (data[start + pos].astype(np.int16) - 48) * 10 + data[start + pos + 1] - 48

    suspect = two(8) >= 29
    if has_time:
        suspect |= two(17) >= 60
    rows = np.flatnonzero(suspect & fixed & valid)
    bad = np.zeros(len(rows), dtype=bool)
    if len(rows):
        sub, idx = pc.take(parsed, pa.array(rows)), start[rows]
        for pos, field in [(8, pc.day)] + ([(17, pc.second)] if has_time else []):
            want = (data[idx + pos].astype(np.int64) - 48) * 10 + data[idx + pos + 1] - 48
            bad |= want != field(sub).to_numpy(zero_copy_only=False)
    return rows[bad], np.flatnonzero(valid & ~fixed)


def _rolls_over(uniq: Any, fmt: str, has_time: bool) -> bool:
    """True when Arrow's ``strptime`` rolls any of these distinct values over (#221)."""
    parsed = pc.strptime(uniq, format=fmt, unit="s", error_is_null=True)
    return _reject_rolled_over(uniq, parsed, has_time) is not parsed


def _reject_rolled_over(text: Any, parsed: Any, has_time: bool) -> Any:
    """``parsed`` with the values Arrow's ``strptime`` rolled over made null: it turns 2023-02-29
    into 2023-03-01 and second 60 into the next minute, where pandas gives NaT (#221)."""
    t_chunks = text.chunks if isinstance(text, pa.ChunkedArray) else [text]
    p_chunks = parsed.chunks if isinstance(parsed, pa.ChunkedArray) else [parsed]
    if [len(c) for c in t_chunks] != [len(c) for c in p_chunks]:
        t_chunks, p_chunks = [pa.concat_arrays(t_chunks)], [pa.concat_arrays(p_chunks)]
    bad_parts, unchecked_parts, base = [], [], 0
    for t, p in zip(t_chunks, p_chunks, strict=True):
        bad, unchecked = _rolled_over_chunk(t, p, has_time)
        bad_parts.append(bad + base)
        unchecked_parts.append(unchecked + base)
        base += len(t)
    bad_rows = np.concatenate(bad_parts)
    unchecked = np.concatenate(unchecked_parts)
    if len(unchecked):  # text of another width: Arrow's strict ISO cast, else field by field
        rows = pa.array(unchecked)
        text_u = pc.take(text, rows)
        if not _try(lambda a: pc.cast(a, pa.timestamp("s")), text_u):
            parsed_u = pc.take(parsed, rows)
            parts = pc.extract_regex(text_u, _ISO_FIELDS.pattern)
            fields = (pc.month, pc.day, pc.hour, pc.minute, pc.second)[: 5 if has_time else 2]
            same = np.ones(len(unchecked), dtype=bool)
            for i, field in enumerate(fields):
                eq = pc.equal(pc.cast(pc.struct_field(parts, [i]), pa.int64()), field(parsed_u))
                same &= pc.fill_null(eq, True).to_numpy(zero_copy_only=False)
            bad_rows = np.concatenate([bad_rows, unchecked[~same]])
    if len(bad_rows) == 0:
        return parsed
    keep = np.ones(len(parsed), dtype=bool)
    keep[bad_rows] = False
    return pc.if_else(pa.array(keep), parsed, pa.scalar(None, parsed.type))


def _coerce_with_dtparse(arr: Any, keep_nulls: bool) -> Any:
    enc = pc.dictionary_encode(arr)
    if isinstance(enc, pa.ChunkedArray):
        enc = enc.combine_chunks()
    parsed = dtparse.coerce_column(enc.dictionary.to_pylist(), first=arr[0].as_py())
    ts = pa.array(parsed, pa.timestamp("us")).take(enc.indices)
    return ts if keep_nulls else pc.drop_null(ts)


# ---------------------------------------------------------------------------
# per-column profiling
# ---------------------------------------------------------------------------


@dataclass
class _Work:
    """Intermediate per-column state kept for PK/FK/correlation."""

    col: _Col
    prof: ColumnProfile
    uniques: Any = None  # pa.Array of distinct non-null values


def _reference_kernel() -> Any:
    from shape.kernel import reference

    return reference


def _pa(x: Any) -> Any:
    """A pyarrow array from a kernel result (the native kernel returns Arrow-capsule objects)."""
    return x if isinstance(x, pa.Array) else pa.array(x)


def _combine(arr: Any) -> Any:
    if isinstance(arr, pa.ChunkedArray):
        return arr.combine_chunks() if arr.num_chunks != 1 else arr.chunk(0)
    return arr


def _require_finite(values: np.ndarray) -> None:
    """The whole-number test does ``series.astype(int)``, which pandas refuses for inf;
    Shape fails on the same input with the same error category (ValueError)."""
    if not np.isfinite(values).all():
        raise ValueError("Cannot convert non-finite values (NA or inf) to integer")


_MAX_BOOL_SPELLINGS = 62
_BOOL_WORDS = {"true", "false", "0", "1", "yes", "no"}
_OBJECT_KINDS = ("objdec", "objtime", "objbin", "objdur", "cat", "objmix")


def _object_key(kind: str, v: Any) -> str:
    """``str(k)`` of a value_counts index entry for the object-dtype columns."""
    if kind == "objbin":
        return repr(bytes(v))
    if kind == "objdur":
        return str(_timedelta(v))
    return str(v)


def _numeric_of_objects(values: list[Any]) -> np.ndarray | None:
    """``pd.to_numeric(object_series, errors="coerce")`` as floats, or None when any value
    would coerce to NaN (the column is then not numeric)."""
    out = np.empty(len(values), dtype=np.float64)
    text_at = [i for i, v in enumerate(values) if isinstance(v, str)]
    for i, v in enumerate(values):
        if not isinstance(v, str):
            out[i] = float(v)
    if text_at:
        try:
            out[text_at] = pc.cast(pa.array([values[i] for i in text_at]), pa.float64()).to_numpy()
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
            return None
    return out


def _object_text(kind: str, v: Any) -> str:
    """``Series.astype(str)`` of one value: bytes are decoded (strictly), the rest use str()."""
    if kind == "objbin":
        return bytes(v).decode("utf-8")
    return _object_key(kind, v)


def _timedelta(v: _dt.timedelta) -> Timedelta:
    return Timedelta(days=v.days, seconds=v.seconds, microseconds=v.microseconds)


def object_entries(c: _Col) -> tuple[list[tuple[Any, int]], int, list[Any]]:
    """``(entries, cardinality, values)`` of an object-dtype column, as pandas counts it:
    ``entries`` is ``value_counts()`` (value, count) count-descending; for a categorical in
    category order with unused categories last (count 0); ``cardinality`` is ``nunique()``;
    ``values`` are the non-null values in row order."""
    arr = _combine(c.arr)
    if c.kind == "cat":
        cats = arr.dictionary.to_pylist()
        codes = [v for v in arr.indices.to_pylist() if v is not None]
        counts_by_cat = [0] * len(cats)
        for code in codes:
            counts_by_cat[code] += 1
        values = [cats[code] for code in codes]
        order = sorted(range(len(cats)), key=lambda i: -counts_by_cat[i])
        return (
            [(cats[i], counts_by_cat[i]) for i in order],
            sum(1 for n in counts_by_cat if n),
            values,
        )
    values = [v for v in arr.to_pylist() if v is not None]
    first: dict[Any, int] = {}
    for v in values:
        first[v] = first.get(v, 0) + 1
    return sorted(first.items(), key=lambda kv: -kv[1]), len(first), values


def _profile_object_column(c: _Col, row_count: int, top_n: int = 500) -> _Work:
    """Object-dtype pandas columns (decimal, time, bytes, timedelta) and categoricals.

    Their values are handled as Python objects, like pandas does; such columns are rare and
    small next to the numeric/text bulk, so this path is not vectorised."""
    kind = c.kind
    n_total = len(_combine(c.arr))
    entries, cardinality, values = object_entries(c)
    n_nn = len(values)
    null_count = n_total - n_nn  # (a union array has no validity bitmap of its own)
    text = [_object_text(kind, v) for v in values]
    ukeys = [_object_key(kind, v) for v, _ in entries]
    row_count = row_count or 0
    cardinality_ratio = cardinality / row_count if row_count else 0.0
    # enum rule (P1-18): the size limits, and the values repeat (distinct <= half the non-null
    # values; a unique column never qualifies)
    is_enum = (
        (cardinality < 200 or (cardinality_ratio < 0.30 and cardinality < 50_000))
        and cardinality > 0
        and 2 * cardinality <= n_nn
        and not (cardinality == row_count and null_count == 0)
    )
    enum_values = value_counts_ext = None
    if n_nn:
        props = [round(n / n_nn, 6) for _, n in entries]
        if is_enum:
            enum_values = _capped(ukeys, props)
        value_counts_ext = _capped(ukeys[:top_n], props[:top_n])

    # ---- column type, numeric stats ------------------------------------------------------
    stype = "string"
    base: ColumnProfile | None = None
    numeric: np.ndarray | None = None
    if kind != "cat" and n_nn:
        lower = {t.lower() for t in {_object_key(kind, v) for v in set(values)}}
        if lower <= _BOOL_WORDS:
            stype = "boolean"
        elif kind == "objdec":
            numeric = np.array([float(v) for v in values], dtype=np.float64)
        elif kind == "objbin":
            try:
                numeric = pc.cast(pa.array(text), pa.float64()).to_numpy()
            except (pa.ArrowInvalid, pa.ArrowNotImplementedError):
                numeric = None
        elif kind == "objmix":
            numeric = _numeric_of_objects(values)
    if numeric is not None:
        base = _profile_column(
            _Col(c.name, "float", pa.chunked_array([pa.array(numeric)])), len(numeric)
        ).prof
        stype = base.dtype
    # ---- min / max ---------------------------------------------------------------------------
    min_value = max_value = None
    if n_nn and kind != "cat":
        try:
            lo, hi = min(values), max(values)
        except TypeError:  # pandas: min() of mixed str/number raises, leave None
            lo = hi = None
        if lo is not None:
            min_value, max_value = (
                (_timedelta(lo), _timedelta(hi)) if kind == "objdur" else (lo, hi)
            )
    pattern = string_length = None
    if stype == "string" and n_nn:
        pattern = detect_pattern(pa.array(text, pa.string()), cardinality)
        lens = np.array([len(t) for t in text], dtype=np.float64)
        string_length = {
            "min": float(lens.min()),
            "mean": round(float(lens.sum(dtype=np.float64) / len(lens)), 2),
            "max": float(lens.max()),
            "p95": float(np.percentile(lens, 95)),
        }
    null_rate = null_count / n_total if n_total and row_count else 0.0
    prof = ColumnProfile(
        name=c.name,
        dtype=stype,
        null_count=null_count,
        null_rate=round(null_rate, 6),
        cardinality=cardinality,
        cardinality_ratio=round(cardinality_ratio, 6),
        is_unique=cardinality == row_count and null_count == 0,
        is_enum=is_enum,
        enum_values=enum_values,
        min_value=min_value,
        max_value=max_value,
        mean=base.mean if base else None,
        std=base.std if base else None,
        distribution=base.distribution if base else None,
        distribution_params=base.distribution_params if base else None,
        pattern=pattern,
        is_primary_key=False,
        is_foreign_key=False,
        fk_ref_table=None,
        quantiles=base.quantiles if base else None,
        string_length=string_length,
        outlier_rate=base.outlier_rate if base else None,
        value_counts_ext=value_counts_ext,
        fit_score=base.fit_score if base else None,
        precision=c.arr.type.precision if kind == "objdec" else None,
        scale=c.arr.type.scale if kind == "objdec" else None,
    )
    return _Work(col=c, prof=prof, uniques=pa.array(ukeys, pa.string()))


def _profile_column(
    c: _Col,
    row_count: int,
    top_n: int = 500,
    keep_uniques: bool = True,
) -> _Work:
    try:
        return _profile_one_column(c, row_count, top_n, keep_uniques)
    except dtparse.ZonedTextError as exc:
        if exc.column is not None:
            raise
        raise dtparse.ZonedTextError(exc.text, c.name) from exc  # say which column, and what to do


def _profile_one_column(c: _Col, row_count: int, top_n: int, keep_uniques: bool) -> _Work:
    kind = c.kind
    if kind in _OBJECT_KINDS:
        return _profile_object_column(c, row_count, top_n)
    arr = c.arr
    wall: Any = None
    zoned: Any = None
    if kind == "dt64" and c.tz:
        # pandas counts a zoned column's instants (two instants in the repeated hour of a DST
        # change are two values, #225) and takes .dt.hour etc. from the wall clock of its zone;
        # keys and min/max keep the zone (and its UTC offset) in their text
        aware = _combine(arr)
        zoned = aware.type
        wall = _local_timestamp(aware, c.tz)
        arr = pc.cast(aware, pa.timestamp(zoned.unit))  # the UTC instants, without the zone

    # ---- non-null values ---------------------------------------------------
    nan_count = inf_count = 0
    if kind == "float":
        a = _combine(arr)
        fvals = a.to_numpy(zero_copy_only=False)  # nulls -> NaN
        # a null is a missing value; NaN and +-inf are values that are not finite (#22): they are
        # counted on their own and kept out of every statistic
        null_count = a.null_count
        finite = np.isfinite(fvals)
        bad = int(len(fvals) - null_count - int(finite.sum()))
        if bad:
            nan_count = int(np.isnan(fvals).sum()) - null_count
            inf_count = bad - nan_count
        nn_np = fvals[finite] if null_count + bad else fvals
        raw_nn = nn_np
        nn_np = nn_np + 0.0  # pandas hashes -0.0 == 0.0 (key printed as the first-seen zero)
        non_null = pa.array(nn_np)
    elif kind == "nullobj":
        null_count = len(arr)
        non_null = pa.array([], pa.string())
        nn_np = None
    else:
        null_count = arr.null_count
        non_null = _combine(pc.drop_null(arr) if null_count else arr)
        nn_np = None
    n_nn = len(non_null)
    null_rate = null_count / row_count if row_count > 0 else 0.0

    # ---- value counts (pandas: hashtable in first-appearance order, stable desc sort).
    # The kernel counts in pandas' order: numeric/timestamp columns by sorting (the sort is
    # reused for quantiles), strings by hashing.
    kernel = get_kernel()
    xs_sorted = None  # sorted non-null numeric values (reused below)
    num_top: dict[str, Any] | None = None  # kernel result for the numeric/timestamp kinds
    counts = np.zeros(0, np.int64)
    uniq: Any = non_null
    if kind == "float":
        key_arr = non_null
    elif kind == "int":
        key_arr = non_null
    elif kind == "dt64" and n_nn:
        key_arr = pc.cast(non_null, pa.int64())
    else:
        key_arr = None
    if n_nn and key_arr is not None:
        num_top = kernel.count_numeric(
            key_arr, top_n, row_count, kind in ("int", "float"), keep_uniques
        )
        cardinality = int(num_top["cardinality"])
        if num_top["sorted"] is not None:
            xs_sorted = _pa(num_top["sorted"]).to_numpy(zero_copy_only=False)
        if keep_uniques:
            uniq = _pa(num_top["uniq"])
            if kind == "dt64":
                uniq = pc.cast(uniq, non_null.type)
    elif n_nn:
        if pa.types.is_string(non_null.type) or pa.types.is_large_string(non_null.type):
            uniq, vcounts = kernel.value_counts_str(non_null)
            uniq = _pa(uniq)
            counts = _pa(vcounts).to_numpy(zero_copy_only=False)
        else:
            vc = pc.value_counts(non_null)
            uniq = vc.field("values")
            counts = vc.field("counts").to_numpy()
        cardinality = len(uniq)
    else:
        cardinality = 0
    cardinality_ratio = cardinality / row_count if row_count > 0 else 0.0
    is_unique = cardinality == row_count and null_count + nan_count + inf_count == 0
    # enum rule (P1-18): the size limits, and the values repeat (distinct <= half the non-null
    # values; a unique column never qualifies)
    is_enum = (
        (cardinality < 200 or (cardinality_ratio < 0.30 and cardinality < 50_000))
        and cardinality > 0
        and 2 * cardinality <= n_nn
        and not is_unique
    )

    # ---- column type -------------------------------------------------------
    numeric = None  # float64 numpy array of numeric values (row order)
    dt_values = None  # pa timestamp array of parsed datetimes (row order, NaT dropped)
    if kind == "bool" or kind == "objbool":
        stype = "boolean"
    elif kind == "int":
        stype = "integer"
        numeric = non_null.to_numpy().astype(np.float64)
    elif kind in ("uint64", "objint"):
        numeric = np.array([float(int(v)) for v in non_null.to_pylist()], dtype=np.float64)
        stype = "integer"
        if kind == "objint":
            # pandas holds these as Python ints in an object column and the baseline checks
            # numeric == numeric.astype(int): the cast overflows, so the column is float (#236)
            with np.errstate(invalid="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                whole = bool(np.array_equal(numeric, numeric.astype(np.int64)))
            stype = "integer" if whole else "float"
    elif kind == "float":
        if n_nn and c.strict:
            _require_finite(nn_np)
        if n_nn and num_top is not None and num_top["all_whole"]:
            stype = "integer"
        else:
            stype = "float"
        numeric = nn_np
    elif kind == "dt64":
        if wall is not None:
            non_null_wall = _combine(pc.drop_null(wall) if null_count else wall)
        else:
            non_null_wall = non_null
        if n_nn:
            ints = pc.cast(non_null_wall, pa.int64()).to_numpy()
            per_day = {"s": 86400, "ms": 86400_000, "us": 86400_000_000, "ns": 86400_000_000_000}[
                non_null.type.unit
            ]
            stype = "date" if not np.any(ints % per_day) else "datetime"
        else:
            stype = "datetime"
        dt_values = non_null_wall
    elif kind == "objdate":
        stype = "string" if n_nn == 0 else "datetime"
        dt_values = pc.cast(non_null, pa.timestamp("s")) if n_nn else None
    elif kind == "nullobj":
        stype = "string"
    else:  # str
        stype = "string"
        if n_nn and not c.text:
            # the six words have at most 62 spellings in all: more distinct values cannot match
            if (
                cardinality <= _MAX_BOOL_SPELLINGS
                and pc.all(
                    pc.is_in(
                        pc.utf8_lower(uniq),
                        value_set=pa.array(["true", "false", "0", "1", "yes", "no"]),
                    )
                ).as_py()
            ):
                stype = "boolean"
            else:
                ok = _try(lambda a: pc.cast(a, pa.float64()), uniq)
                u = pc.cast(uniq, pa.float64()).to_numpy() if ok else None
                if u is not None and np.isnan(u).any():
                    ok = (
                        False  # a NaN word: pandas' to_numeric gives NaN, the column is text (#224)
                    )
                if ok:
                    assert u is not None
                    if c.strict:
                        _require_finite(u)
                    stype = "integer" if all_whole(u) else "float"
                    numeric = pc.cast(non_null, pa.float64()).to_numpy()
                else:
                    # pandas: to_datetime(format="mixed") must accept every value.  If the
                    # strict guessed-format parse (needed later anyway) already accepts all
                    # rows, that implies it; otherwise check the distinct values.
                    # ISO text (the common case) is parsed by Arrow in bulk; anything else is
                    # checked on its distinct values first, stopping at the first that does not
                    # parse, so a column of ordinary text costs one failed parse, not one per
                    # distinct value.
                    dt_try = (
                        _coerce_datetime_strings(non_null, keep_nulls=True, uniq=uniq)
                        if _first_is_iso(non_null)
                        else None
                    )
                    if dt_try is not None and dt_try.null_count == 0:
                        stype = "datetime"
                        dt_values = dt_try
                    elif _all_parse_datetime(uniq):
                        stype = "datetime"
                        if dt_try is None:
                            dt_try = _coerce_datetime_strings(non_null, keep_nulls=True, uniq=uniq)
                        dt_values = (
                            pc.drop_null(dt_try)
                            if dt_try is not None
                            else _coerce_datetime_strings(non_null, uniq=uniq)
                        )

    # ---- enum + value_counts_ext ------------------------------------------
    enum_values = None
    value_counts_ext = None
    # a text column whose values are (nearly) all different has no frequencies to report: its top
    # values would be an arbitrary few of them, stored whole (#37)
    arbitrary_top = (
        kind == "str" and stype == "string" and cardinality > top_n and cardinality >= 0.95 * n_nn
    )
    if n_nn and not arbitrary_top:
        if num_top is not None:
            top_keys = _pa(num_top["keys"])
            top_counts = _pa(num_top["counts"]).to_numpy(zero_copy_only=False)
            if kind == "dt64":
                top_keys = pc.cast(top_keys, non_null.type)
        else:
            need = cardinality if is_enum else min(top_n, cardinality)
            top = _pa(kernel.top_indices(pa.array(counts), need)).to_numpy(zero_copy_only=False)
            top_keys = uniq.take(pa.array(top))
            top_counts = counts[top]
        keys = _keys_py(top_keys, kind)
        if zoned is not None:
            keys = [str(v) for v in _aware_datetimes(pc.cast(top_keys, zoned), zoned.tz)]
        if kind == "float" and "0.0" in keys:
            zeros = np.flatnonzero(raw_nn == 0)
            if len(zeros) and np.signbit(raw_nn[zeros[0]]):
                keys[keys.index("0.0")] = "-0.0"
        props = top_counts / n_nn
        rounded = _round6(props)
        if is_enum:
            enum_values = _capped(keys, rounded)
        value_counts_ext = _capped(keys[:top_n], rounded[:top_n])

    # ---- min / max (pandas types) ------------------------------------------
    min_value = max_value = None
    if n_nn:
        if kind == "float":
            min_value, max_value = float(raw_nn.min()), float(raw_nn.max())
        else:
            mm = pc.min_max(non_null)
            lo, hi = mm["min"].as_py(), mm["max"].as_py()
            if kind == "dt64":
                if zoned is not None:
                    instants = pc.cast(pa.array([lo, hi], non_null.type), zoned)
                    lo, hi = _aware_datetimes(instants, zoned.tz)
                lo, hi = _to_timestamp(lo), _to_timestamp(hi)
            elif kind in ("uint64", "objint"):
                lo, hi = int(lo), int(hi)
            min_value, max_value = lo, hi

    # ---- numeric stats / distribution / quantiles ---------------------------
    mean_val = std_val = None
    dist_name = dist_params = None
    quantiles = None
    outlier_rate_val = None
    fit_score_val = None
    if stype in ("integer", "float") and numeric is not None and len(numeric) > 0:
        numeric = np.ascontiguousarray(numeric, dtype=np.float64)
        cnt = len(numeric)
        xs = None
        if xs_sorted is not None and kind in ("int", "float"):
            xs = xs_sorted.astype(np.float64, copy=False)
        st = kernel.numeric_stats(pa.array(numeric), pa.array(xs) if xs is not None else None)
        mean_val, std_val = st["mean"], st["std"]
        if cnt < 2:
            std_val = None  # a spread needs two values; NaN would not be valid JSON (#22)
        fitted = _kernel_detect_distribution(numeric)
        dist_name, dist_params = fitted["distribution"], fitted["distribution_params"]
        fit_score_val = fitted["fit_score"]
        if st["has_quantiles"]:
            vals = st["quantiles"]
            quantiles = {f"p{p}": round(float(v), 6) for p, v in zip(_PCTS, vals[:9], strict=True)}
            quantiles["p0_5"] = round(float(vals[9]), 6)
            quantiles["p99_5"] = round(float(vals[10]), 6)
            outlier_rate_val = 0.0 if st["outliers"] is None else round(st["outliers"] / cnt, 6)

    # ---- strings -------------------------------------------------------------
    pattern = None
    string_length = None
    rates: dict[str, float] = {}
    contains: dict[str, float] = {}
    if stype == "string" and n_nn:
        pattern = detect_pattern(non_null, cardinality)
        if kind == "str":
            rates, contains = pattern_rates(uniq, counts, n_nn, pattern)
        lens = pc.utf8_length(non_null).to_numpy()
        string_length = {
            "min": float(lens.min()),
            "mean": round(float(lens.sum(dtype=np.float64) / len(lens)), 2),
            "max": float(lens.max()),
            "p95": float(np.percentile(lens, 95)),
        }

    # ---- temporal ------------------------------------------------------------
    hour_h = dow_h = temporal = None
    if stype in ("date", "datetime") and n_nn:
        ts = dt_values
        if ts is None or len(ts) == 0:
            hour_h = [1.0 / 24] * 24
            dow_h = [1.0 / 7] * 7
        else:
            tc = (kernel if ts.type.tz is None else _reference_kernel()).temporal_counts(ts)
            hc = np.asarray(tc["hour"], dtype=float)
            hour_h = _round6(hc / hc.sum())
            dc = np.asarray(tc["dow"], dtype=float)
            dow_h = _round6(dc / dc.sum())
            # np.percentile(years, [1, 99]) from the year histogram (exact same values)
            y0 = int(tc["year0"])
            ycounts = np.asarray(tc["years"], dtype=np.int64)
            lo_year = int(_pct_from_counts(ycounts, y0, 1))
            hi_year = int(_pct_from_counts(ycounts, y0, 99))
            if hi_year < lo_year:
                hi_year = lo_year
            span = hi_year - lo_year + 1
            where = np.clip(np.arange(len(ycounts)) + y0 - lo_year, 0, span - 1)
            yr = np.bincount(where, weights=ycounts, minlength=span)
            mc = np.asarray(tc["month"], dtype=float)
            temporal = {
                "lo_year": lo_year,
                "hi_year": hi_year,
                "year_weights": _round6(yr / yr.sum()),
                "month_weights": _round6(mc / mc.sum()),
            } or None

    prof = ColumnProfile(
        name=c.name,
        dtype=stype,
        null_count=null_count,
        null_rate=round(null_rate, 6),
        cardinality=cardinality,
        cardinality_ratio=round(cardinality_ratio, 6),
        is_unique=is_unique,
        is_enum=is_enum,
        enum_values=enum_values,
        min_value=min_value,
        max_value=max_value,
        mean=mean_val,
        std=std_val,
        distribution=dist_name,
        distribution_params=dist_params,
        pattern=pattern,
        is_primary_key=False,
        is_foreign_key=False,
        fk_ref_table=None,
        quantiles=quantiles,
        hour_histogram=hour_h,
        dow_histogram=dow_h,
        temporal_histogram=temporal,
        string_length=string_length,
        outlier_rate=outlier_rate_val,
        value_counts_ext=value_counts_ext,
        fit_score=fit_score_val,
        nan_count=nan_count,
        inf_count=inf_count,
        pattern_rates=rates or None,
        pattern_contains_rates=contains or None,
    )
    return _Work(col=c, prof=prof, uniques=uniq if (keep_uniques or num_top is None) else None)


def _pct_from_counts(counts: np.ndarray, offset: int, q: float) -> float:
    """np.percentile(int_values, q) computed from a histogram of the integer values (same
    virtual index arithmetic and _lerp as numpy, so the result is identical)."""
    n = int(counts.sum())
    prev, nxt, gamma = _linear_index(n, [q])
    cum = np.cumsum(counts)
    idx = np.where(np.concatenate([prev, nxt]) < 0, n - 1, np.concatenate([prev, nxt]))
    vals = np.searchsorted(cum, idx, side="right") + offset
    return float(_lerp(vals[:1], vals[1:], gamma)[0])
