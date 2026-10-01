"""Per-column profiling."""

from __future__ import annotations

import datetime as _dt
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, cast

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.kernel.dispatch import get_kernel
from shape.kernel.reference.exact import PCTS as _PCTS
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
    return Timestamp(
        v.year, v.month, v.day, v.hour, v.minute, v.second, v.microsecond, tzinfo=v.tzinfo
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


def _coerce_datetime_strings(arr: Any, keep_nulls: bool = False) -> Any:
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
    out = pc.strptime(arr, format=fmt, unit="s", error_is_null=True)
    return out if keep_nulls else pc.drop_null(out)


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
    is_enum = (
        cardinality < 200 or (cardinality_ratio < 0.30 and cardinality < 50_000)
    ) and cardinality > 0
    enum_values = value_counts_ext = None
    if n_nn:
        props = [round(n / n_nn, 6) for _, n in entries]
        if is_enum:
            enum_values = dict(zip(ukeys, props, strict=True))
        value_counts_ext = dict(zip(ukeys[:top_n], props[:top_n], strict=True))

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
    )
    return _Work(col=c, prof=prof, uniques=pa.array(ukeys, pa.string()))


def _profile_column(
    c: _Col,
    row_count: int,
    top_n: int = 500,
    keep_uniques: bool = True,
) -> _Work:
    kind = c.kind
    if kind in _OBJECT_KINDS:
        return _profile_object_column(c, row_count, top_n)
    arr = c.arr
    tzmap: dict[Any, Any] | None = None
    if kind == "dt64" and c.tz:
        # pandas' .dt.hour etc. use the wall clock of the column's zone; keys and min/max
        # keep the zone (and its UTC offset) in their text
        aware = _combine(arr)
        arr = pc.local_timestamp(aware)
        tzmap = dict(zip(_combine(arr).to_pylist(), aware.to_pylist(), strict=True))

    # ---- non-null values ---------------------------------------------------
    if kind == "float":
        a = _combine(arr)
        fvals = a.to_numpy(zero_copy_only=False)  # nulls -> NaN
        nan_mask = np.isnan(fvals)
        null_count = int(nan_mask.sum())
        nn_np = fvals[~nan_mask] if null_count else fvals
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
    is_unique = cardinality == row_count and null_count == 0
    is_enum = (
        cardinality < 200 or (cardinality_ratio < 0.30 and cardinality < 50_000)
    ) and cardinality > 0

    # ---- column type -------------------------------------------------------
    numeric = None  # float64 numpy array of numeric values (row order)
    dt_values = None  # pa timestamp array of parsed datetimes (row order, NaT dropped)
    if kind == "bool" or kind == "objbool":
        stype = "boolean"
    elif kind == "int":
        stype = "integer"
        numeric = non_null.to_numpy().astype(np.float64)
    elif kind in ("uint64", "objint"):
        stype = "integer"
        numeric = np.array([float(int(v)) for v in non_null.to_pylist()], dtype=np.float64)
    elif kind == "float":
        if n_nn and c.strict:
            _require_finite(nn_np)
        if n_nn and num_top is not None and num_top["all_whole"]:
            stype = "integer"
        else:
            stype = "float"
        numeric = nn_np
    elif kind == "dt64":
        if n_nn:
            ints = pc.cast(non_null, pa.int64()).to_numpy()
            per_day = {"s": 86400, "ms": 86400_000, "us": 86400_000_000, "ns": 86400_000_000_000}[
                non_null.type.unit
            ]
            stype = "date" if not np.any(ints % per_day) else "datetime"
        else:
            stype = "datetime"
        dt_values = non_null
    elif kind == "objdate":
        stype = "string" if n_nn == 0 else "datetime"
        dt_values = pc.cast(non_null, pa.timestamp("s")) if n_nn else None
    elif kind == "nullobj":
        stype = "string"
    else:  # str
        stype = "string"
        if n_nn:
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
                if ok:
                    u = pc.cast(uniq, pa.float64()).to_numpy()
                    if c.strict:
                        _require_finite(u)
                    stype = "integer" if np.all(u == u.astype(np.int64)) else "float"
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
                        _coerce_datetime_strings(non_null, keep_nulls=True)
                        if _first_is_iso(non_null)
                        else None
                    )
                    if dt_try is not None and dt_try.null_count == 0:
                        stype = "datetime"
                        dt_values = dt_try
                    elif _all_parse_datetime(uniq):
                        stype = "datetime"
                        if dt_try is None:
                            dt_try = _coerce_datetime_strings(non_null, keep_nulls=True)
                        dt_values = (
                            pc.drop_null(dt_try)
                            if dt_try is not None
                            else _coerce_datetime_strings(non_null)
                        )

    # ---- enum + value_counts_ext ------------------------------------------
    enum_values = None
    value_counts_ext = None
    if n_nn:
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
        if tzmap is not None:
            keys = [str(tzmap[v]) for v in top_keys.to_pylist()]
        if kind == "float" and "0.0" in keys:
            zeros = np.flatnonzero(raw_nn == 0)
            if len(zeros) and np.signbit(raw_nn[zeros[0]]):
                keys[keys.index("0.0")] = "-0.0"
        props = top_counts / n_nn
        rounded = _round6(props)
        if is_enum:
            enum_values = dict(zip(keys, rounded, strict=True))
        value_counts_ext = dict(zip(keys[:top_n], rounded[:top_n], strict=True))

    # ---- min / max (pandas types) ------------------------------------------
    min_value = max_value = None
    if n_nn:
        if kind == "float":
            min_value, max_value = float(raw_nn.min()), float(raw_nn.max())
        else:
            mm = pc.min_max(non_null)
            lo, hi = mm["min"].as_py(), mm["max"].as_py()
            if kind == "dt64":
                lo, hi = (
                    _to_timestamp(tzmap[lo] if tzmap else lo),
                    _to_timestamp(tzmap[hi] if tzmap else hi),
                )
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
    if stype == "string" and n_nn:
        pattern = detect_pattern(non_null, cardinality)
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
