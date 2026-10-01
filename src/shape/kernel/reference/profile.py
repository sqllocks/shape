"""Pure-Python twin of the fused profile kernel (``rust/shape-kernel/src/profile.rs``).

Same inputs, same output dict; written independently of the Rust code (per-batch numpy and
pyarrow, the Python sketches) so that the differential tests are a real cross-check. See the
Rust module for what each statistic means.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
import struct
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from . import hashing as H

QUANTILES = (0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0)
BOUNDED_TOP = 64
_OCT = r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
PATTERNS = {
    "email": r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
    "uuid": r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
    "ssn": r"^\d{3}-\d{2}-\d{4}$",
    "mac": r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$|^([0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}$",
    "ipv4": rf"^{_OCT}\.{_OCT}\.{_OCT}\.{_OCT}$",
    "ipv6": (
        r"^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$"
        r"|^(?:[0-9a-fA-F]{1,4}:){1,7}:$"
        r"|^(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$"
        r"|^::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}$"
        r"|^::$"
    ),
    "iban": r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$",
    "postal": r"^\d{5}(-\d{4})?$",
    "date": r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$",
    "phone": r"^[\+]?[\d\s\-\(\)\.]{7,20}$",
    "currency": r"^[A-Z]{3}$",
    "language": r"^[a-z]{2}(-[A-Z]{2})?$",
}
_COMPILED = {k: re.compile(v) for k, v in PATTERNS.items()}
_EPOCH = _dt.datetime(1970, 1, 1)


def _is_string_view(t: Any) -> bool:
    check = getattr(pa.types, "is_string_view", None)
    return bool(check and check(t))


def _kind(t: Any) -> str:
    if pa.types.is_integer(t):
        return "int"
    if pa.types.is_floating(t) or pa.types.is_decimal128(t):
        return "float"
    if pa.types.is_boolean(t):
        return "bool"
    if pa.types.is_string(t) or pa.types.is_large_string(t) or _is_string_view(t):
        return "text"
    if pa.types.is_date32(t) or pa.types.is_date64(t) or pa.types.is_timestamp(t):
        return "temporal"
    return "other"


def _key_hash(key: tuple[int, Any]) -> int:
    tag, v = key
    if tag == 0:
        return int(H._hash_bytes(H._int_bytes(v), 0))
    if tag == 1:
        b = H.float_bytes(struct.unpack("<d", struct.pack("<Q", v))[0])
        assert b is not None
        return int(H._hash_bytes(b, 0))
    if tag == 2:
        return int(H._hash_bytes(H._i64(H.TAG_TS, v), 0))
    return int(H._hash_bytes(bytes([H.TAG_STR]) + v.encode("utf-8"), 0))


def _sketches() -> Any:
    from . import pysketch

    return pysketch


class _Tracker:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        if mode == "exact":
            self.counts: dict[tuple[int, Any], list[int]] = {}
        else:
            sk = _sketches()
            self.hll = sk.HyperLogLog(14)
            self.ss = sk.SpaceSaving(BOUNDED_TOP)

    def add(self, key: tuple[int, Any], ordinal: int) -> None:
        if self.mode == "exact":
            e = self.counts.get(key)
            if e is None:
                self.counts[key] = [1, ordinal]
            else:
                e[0] += 1
        else:
            self.hll.update_hashed(_key_hash(key))
            self.ss.update(key)

    def merge(self, o: _Tracker, offset: int) -> None:
        if self.mode == "exact":
            for k, (c, first) in o.counts.items():
                e = self.counts.get(k)
                if e is None:
                    self.counts[k] = [c, first + offset]
                else:
                    e[0] += c
                    e[1] = min(e[1], first + offset)
        else:
            self.hll.merge(o.hll)
            self.ss.merge(o.ss)

    def distinct(self) -> tuple[float, bool]:
        if self.mode == "exact":
            return float(len(self.counts)), True
        return float(self.hll.estimate()), False

    def top(self, n: int) -> list[tuple[Any, Any, Any, Any]]:
        if self.mode == "exact":
            rows = sorted(self.counts.items(), key=lambda kv: (-kv[1][0], kv[1][1]))
            return [(_value(k), c, 0, f) for k, (c, f) in rows[:n]]
        return [(_value(k), c, e, None) for k, c, e in self.ss.top(self.ss.capacity)][:n]


def _value(key: tuple[int, Any]) -> Any:
    tag, v = key
    if tag == 1:
        return float(struct.unpack("<d", struct.pack("<Q", v))[0])
    return v


def _float_key(x: float) -> tuple[int, int]:
    x = 0.0 if x == 0.0 else x
    return (1, struct.unpack("<Q", struct.pack("<d", x))[0])


def _histogram_quantile(hist: dict[int, int], q: float) -> float:
    """``np.quantile(np.repeat(values, counts), q, method="linear")`` without materialising one
    element per row (memory must not grow with the row count): walk the sorted histogram to the
    two neighbouring order statistics and interpolate the way numpy does."""
    n = sum(hist.values())
    pos = q * (n - 1)
    lo = int(math.floor(pos))
    frac = pos - lo
    a = b = float(max(hist))
    seen = 0
    keys = sorted(hist)
    for i, v in enumerate(keys):
        seen += hist[v]
        if seen > lo:
            a = float(v)
            b = a if seen > lo + 1 else float(keys[min(i + 1, len(keys) - 1)])
            break
    diff = b - a
    return b - diff * (1.0 - frac) if frac >= 0.5 else a + diff * frac


class _Column:
    def __init__(self, name: str, typ: Any, mode: str) -> None:
        self.name, self.typ, self.mode = name, typ, mode
        self.kind = _kind(typ)
        self.count = self.nulls = 0
        if self.kind in ("int", "float"):
            self.nan = self.pos_inf = self.neg_inf = self.finite = 0
            self.mean = 0.0
            self.m2 = 0.0
            self.min: Any = None
            self.max: Any = None
            self.values: list[float] = []
            self.kll = _sketches().KLL(200) if mode == "bounded" else None
        if self.kind in ("int", "float", "text", "temporal"):
            self.tracker = _Tracker(mode)
        if self.kind == "bool":
            self.true = self.false = 0
        if self.kind == "text":
            self.tmin: str | None = None
            self.tmax: str | None = None
            self.lengths: dict[int, int] = {}
            self.patterns = dict.fromkeys(PATTERNS, 0)
        if self.kind == "temporal":
            self.tmin_us: int | None = None
            self.tmax_us: int | None = None
            self.hour = [0] * 24
            self.dow = [0] * 7
            self.month = [0] * 12
            self.year: dict[int, int] = {}

    # ------------------------------------------------------------ update
    def update(self, arr: Any, row0: int) -> None:
        n = len(arr)
        self.count += n
        self.nulls += arr.null_count
        k = self.kind
        if k == "other":
            return
        valid = arr.is_valid().to_numpy(zero_copy_only=False)
        if k == "bool":
            vals = arr.fill_null(False).to_numpy(zero_copy_only=False)
            self.true += int(np.count_nonzero(vals & valid))
            self.false += int(np.count_nonzero(~vals & valid))
        elif k == "int":
            self._update_ints(arr, valid, row0)
        elif k == "float":
            self._update_floats(arr, valid, row0)
        elif k == "text":
            self._update_text(arr, valid, row0)
        else:
            self._update_temporal(arr, valid, row0)

    def _finite_batch(self, xs: np.ndarray) -> None:
        if not len(xs):
            return
        n2 = len(xs)
        m2_mean = float(xs.mean())
        m2 = float(((xs - m2_mean) ** 2).sum())
        n1 = self.finite
        d = m2_mean - self.mean
        tot = n1 + n2
        self.m2 += m2 + d * d * n1 * n2 / tot
        self.mean += d * n2 / tot
        self.finite = tot
        if self.kll is not None:
            for x in xs.tolist():
                self.kll.update(x)
        else:
            self.values.extend(xs.tolist())

    def _update_ints(self, arr: Any, valid: np.ndarray, row0: int) -> None:
        ints = arr.to_pylist()
        xs = []
        for i, v in enumerate(ints):
            if v is None:
                continue
            self.tracker.add((0, v), row0 + i)
            self.min = v if self.min is None or v < self.min else self.min
            self.max = v if self.max is None or v > self.max else self.max
            xs.append(float(v))
        self._finite_batch(np.array(xs, dtype=np.float64))

    def _update_floats(self, arr: Any, valid: np.ndarray, row0: int) -> None:
        if pa.types.is_decimal128(arr.type):
            scale = arr.type.scale
            vals = [
                None if d is None else int(d.scaleb(scale).to_integral_value()) / 10.0**scale
                for d in arr.to_pylist()
            ]
        else:
            vals = arr.cast(pa.float64()).to_pylist()
        xs = []
        for i, x in enumerate(vals):
            if x is None:
                continue
            if math.isnan(x):
                self.nan += 1
                continue
            self.tracker.add(_float_key(x), row0 + i)
            if math.isinf(x):
                if x > 0:
                    self.pos_inf += 1
                else:
                    self.neg_inf += 1
                continue
            xs.append(x)
            self.min = x if self.min is None or x < self.min else self.min
            self.max = x if self.max is None or x > self.max else self.max
        self._finite_batch(np.array(xs, dtype=np.float64))

    def _update_text(self, arr: Any, valid: np.ndarray, row0: int) -> None:
        for i, s in enumerate(arr.to_pylist()):
            if s is None:
                continue
            ln = len(s)
            self.lengths[ln] = self.lengths.get(ln, 0) + 1
            if self.tmin is None or s < self.tmin:
                self.tmin = s
            if self.tmax is None or s > self.tmax:
                self.tmax = s
            if len(s.encode("utf-8")) <= 200:
                for name, rx in _COMPILED.items():
                    if rx.search(s):
                        self.patterns[name] += 1
            self.tracker.add((3, s), row0 + i)

    def _update_temporal(self, arr: Any, valid: np.ndarray, row0: int) -> None:
        t = arr.type
        if pa.types.is_date32(t):
            raw = arr.cast(pa.int32()).to_pylist()
            us = [None if v is None else H._wrap_i64(v * 86_400_000_000) for v in raw]
        elif pa.types.is_date64(t):
            raw = arr.cast(pa.int64()).to_pylist()
            us = [None if v is None else H._wrap_i64(v * 1000) for v in raw]
        else:
            raw = arr.cast(pa.int64()).to_pylist()
            us = [None if v is None else H._time_us(v, t.unit) for v in raw]
        for i, v in enumerate(us):
            if v is None:
                continue
            self.tracker.add((2, v), row0 + i)
            self.tmin_us = v if self.tmin_us is None or v < self.tmin_us else self.tmin_us
            self.tmax_us = v if self.tmax_us is None or v > self.tmax_us else self.tmax_us
            days, rem = divmod(v, 86_400_000_000)
            self.hour[rem // 3_600_000_000] += 1
            self.dow[(days + 3) % 7] += 1
            d = (_EPOCH + _dt.timedelta(days=days)).date()
            self.month[d.month - 1] += 1
            self.year[d.year] = self.year.get(d.year, 0) + 1

    # ------------------------------------------------------------- merge
    def merge(self, o: _Column, offset: int) -> None:
        self.count += o.count
        self.nulls += o.nulls
        k = self.kind
        if k in ("int", "float"):
            self.nan += o.nan
            self.pos_inf += o.pos_inf
            self.neg_inf += o.neg_inf
            if o.finite:
                tot = self.finite + o.finite
                d = o.mean - self.mean
                self.m2 += o.m2 + d * d * self.finite * o.finite / tot
                self.mean += d * o.finite / tot
                self.finite = tot
            if o.min is not None:
                self.min = o.min if self.min is None else min(self.min, o.min)
            if o.max is not None:
                self.max = o.max if self.max is None else max(self.max, o.max)
            self.values.extend(o.values)
            if self.kll is not None:
                self.kll.merge(o.kll)
        if k in ("int", "float", "text", "temporal"):
            self.tracker.merge(o.tracker, offset)
        if k == "bool":
            self.true += o.true
            self.false += o.false
        if k == "text":
            for ln, c in o.lengths.items():
                self.lengths[ln] = self.lengths.get(ln, 0) + c
            for name in self.patterns:
                self.patterns[name] += o.patterns[name]
            if o.tmin is not None and (self.tmin is None or o.tmin < self.tmin):
                self.tmin = o.tmin
            if o.tmax is not None and (self.tmax is None or o.tmax > self.tmax):
                self.tmax = o.tmax
        if k == "temporal":
            if o.tmin_us is not None:
                self.tmin_us = o.tmin_us if self.tmin_us is None else min(self.tmin_us, o.tmin_us)
            if o.tmax_us is not None:
                self.tmax_us = o.tmax_us if self.tmax_us is None else max(self.tmax_us, o.tmax_us)
            for a, b in ((self.hour, o.hour), (self.dow, o.dow), (self.month, o.month)):
                for i, c in enumerate(b):
                    a[i] += c
            for y, c in o.year.items():
                self.year[y] = self.year.get(y, 0) + c

    # ---------------------------------------------------------- finalize
    def finalize(self, top_n: int) -> dict[str, Any]:
        d: dict[str, Any] = {
            "name": self.name,
            "kind": self.kind,
            "count": self.count,
            "null_count": self.nulls,
        }
        k = self.kind
        if k == "bool":
            d["true_count"], d["false_count"] = self.true, self.false
        if k in ("int", "float"):
            d.update(
                nan_count=self.nan,
                pos_inf_count=self.pos_inf,
                neg_inf_count=self.neg_inf,
                finite_count=self.finite,
                min=self.min,
                max=self.max,
                mean=self.mean if self.finite else None,
                m2=self.m2 if self.finite else None,
            )
        if k in ("int", "float", "text", "temporal"):
            dist, exact = self.tracker.distinct()
            d["distinct"], d["distinct_exact"] = dist, exact
            d["top"] = self.tracker.top(top_n if exact else min(top_n, BOUNDED_TOP))
        if k in ("int", "float"):
            q: dict[float, float] = {}
            if self.finite:
                if self.kll is not None:
                    q = {p: self.kll.quantile(p) for p in QUANTILES}
                else:
                    arr = np.sort(np.array(self.values, dtype=np.float64))
                    q = {p: float(np.quantile(arr, p, method="linear")) for p in QUANTILES}
            d["quantiles"] = q
        if k == "text":
            nn = self.count - self.nulls
            d["min"], d["max"] = self.tmin, self.tmax
            length: dict[str, Any] = {"count": nn}
            if nn:
                length.update(
                    min=min(self.lengths),
                    max=max(self.lengths),
                    mean=sum(x * c for x, c in self.lengths.items()) / nn,
                    p95=_histogram_quantile(self.lengths, 0.95),
                    hist=dict(sorted(self.lengths.items())),
                )
            d["length"] = length
            d["patterns"] = dict(self.patterns)
        if k == "temporal":
            d.update(
                min=self.tmin_us,
                max=self.tmax_us,
                hour_hist=list(self.hour),
                dow_hist=list(self.dow),
                month_hist=list(self.month),
                year_hist=dict(sorted(self.year.items())),
            )
        return d


class ProfileState:
    """Twin of ``shape._kernel.ProfileState``."""

    def __init__(self, schema: Any, mode: str = "exact") -> None:
        if mode not in ("exact", "bounded"):
            raise ValueError(f"mode must be 'exact' or 'bounded', got {mode!r}")
        self.schema = pa.schema(schema)
        self._mode = mode
        self._rows = 0
        self._cols = [_Column(f.name, f.type, mode) for f in self.schema]

    @property
    def rows(self) -> int:
        return self._rows

    @property
    def mode(self) -> str:
        return self._mode

    def update(self, batch: Any) -> None:
        batch = batch if isinstance(batch, pa.RecordBatch) else pa.record_batch(batch)
        if [f.type for f in batch.schema] != [f.type for f in self.schema]:
            raise ValueError("batch schema differs from the profile schema")
        for col, arr in zip(self._cols, batch.columns, strict=True):
            col.update(arr, self._rows)
        self._rows += batch.num_rows

    def merge(self, other: ProfileState) -> None:
        if self._mode != other._mode or self.schema != other.schema:
            raise ValueError("cannot merge profiles with different schemas or modes")
        for a, b in zip(self._cols, other._cols, strict=True):
            a.merge(b, self._rows)
        self._rows += other._rows

    def finalize(self, top_n: int = 500) -> dict[str, Any]:
        return {
            "rows": self._rows,
            "mode": self._mode,
            "columns": [c.finalize(top_n) for c in self._cols],
        }
