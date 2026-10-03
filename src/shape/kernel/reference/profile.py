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
from .sketch import _check_kll, _check_registers

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

    # ---------------------------------------------------------- snapshot
    def write_snapshot(self, w: _Writer) -> None:
        w.pack("BQQ", _KIND_CODE[self.kind], self.count, self.nulls)
        k = self.kind
        if k == "bool":
            w.pack("QQ", self.true, self.false)
        elif k in ("int", "float"):
            w.pack("QQQQ", self.nan, self.pos_inf, self.neg_inf, self.finite)
            w.pack("dd", self.mean, self.m2)
            as_f = None if self.min is None else float(self.min)
            w.opt(as_f, "d")
            w.opt(None if self.max is None else float(self.max), "d")
            w.opt(self.min if k == "int" else None, "i128")
            w.opt(self.max if k == "int" else None, "i128")
            if self.kll is None:
                raise ValueError(_BOUNDED_ONLY)
            _write_kll(w, self.kll)
            _write_tracker(w, self.tracker)
        elif k == "text":
            w.opt_text(self.tmin)
            w.opt_text(self.tmax)
            w.pack("I", len(self.lengths))
            for ln in sorted(self.lengths):
                w.pack("QQ", ln, self.lengths[ln])
            w.pack("12Q", *self.patterns.values())
            _write_tracker(w, self.tracker)
        elif k == "temporal":
            w.opt(self.tmin_us, "q")
            w.opt(self.tmax_us, "q")
            w.pack("24Q7Q12Q", *self.hour, *self.dow, *self.month)
            w.pack("I", len(self.year))
            for y in sorted(self.year):
                w.pack("qQ", y, self.year[y])
            _write_tracker(w, self.tracker)

    def read_snapshot(self, r: _Reader) -> None:
        if r.unpack("B") != _KIND_CODE[self.kind]:
            raise ValueError(f"snapshot column kind differs from the schema for {self.name!r}")
        self.count, self.nulls = r.unpack("Q"), r.unpack("Q")
        k = self.kind
        if k == "bool":
            self.true, self.false = r.unpack("Q"), r.unpack("Q")
        elif k in ("int", "float"):
            self.nan, self.pos_inf, self.neg_inf, self.finite = (r.unpack("Q") for _ in range(4))
            self.mean, self.m2 = r.unpack("d"), r.unpack("d")
            fmin, fmax = r.opt("d"), r.opt("d")
            imin, imax = r.opt("i128"), r.opt("i128")
            self.min, self.max = (imin, imax) if k == "int" else (fmin, fmax)
            self.kll = _read_kll(r)
            self.tracker = _read_tracker(r)
        elif k == "text":
            self.tmin, self.tmax = r.opt_text(), r.opt_text()
            self.lengths = {}
            for _ in range(r.unpack("I")):
                ln = r.unpack("Q")
                self.lengths[ln] = r.unpack("Q")
            for name in self.patterns:
                self.patterns[name] = r.unpack("Q")
            self.tracker = _read_tracker(r)
        elif k == "temporal":
            self.tmin_us, self.tmax_us = r.opt("q"), r.opt("q")
            for hist in (self.hour, self.dow, self.month):
                for i in range(len(hist)):
                    hist[i] = r.unpack("Q")
            self.year = {}
            for _ in range(r.unpack("I")):
                y = r.unpack("q")
                self.year[y] = r.unpack("Q")
            self.tracker = _read_tracker(r)

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


# ---------------------------------------------------------------- snapshot
# Bounded-mode state as bytes, identical to the Rust kernel's format (see
# ``rust/shape-kernel/src/profile.rs``): a restored state continues exactly as the original.

_SNAP_MAGIC = b"SHPS"
_SNAP_VERSION = 1
_KIND_CODE = {"other": 0, "int": 1, "float": 2, "bool": 3, "text": 4, "temporal": 5}
_BOUNDED_ONLY = "snapshots support bounded mode only"


class _Writer:
    def __init__(self) -> None:
        self.buf = bytearray()

    def pack(self, fmt: str, *v: Any) -> None:
        self.buf += struct.pack("<" + fmt, *v)

    def i128(self, v: int) -> None:
        self.buf += int(v).to_bytes(16, "little", signed=True)

    def text(self, v: str) -> None:
        raw = v.encode("utf-8")
        self.pack("I", len(raw))
        self.buf += raw

    def opt(self, v: Any, fmt: str) -> None:
        self.pack("B", v is not None)
        if fmt == "i128":
            self.i128(0 if v is None else v)
        else:
            self.pack(fmt, 0 if v is None else v)

    def opt_text(self, v: str | None) -> None:
        self.pack("B", v is not None)
        if v is not None:
            self.text(v)


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data, self.i = data, 0

    def take(self, n: int) -> bytes:
        if self.i + n > len(self.data):
            raise ValueError("snapshot is truncated")
        out = self.data[self.i : self.i + n]
        self.i += n
        return out

    def unpack(self, fmt: str) -> Any:
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))[0]

    def i128(self) -> int:
        return int.from_bytes(self.take(16), "little", signed=True)

    def text(self) -> str:
        try:
            return self.take(self.unpack("I")).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("snapshot holds invalid UTF-8") from exc

    def opt(self, fmt: str) -> Any:
        flag = self.unpack("B")
        v = self.i128() if fmt == "i128" else self.unpack(fmt)
        return v if flag else None

    def opt_text(self) -> str | None:
        return self.text() if self.unpack("B") else None


def _write_key(w: _Writer, key: tuple[int, Any]) -> None:
    tag, v = key
    w.pack("B", tag)
    if tag == 0:
        w.i128(v)
    elif tag == 1:
        w.pack("Q", v)
    elif tag == 2:
        w.pack("q", v)
    else:
        w.text(v)


def _read_key(r: _Reader) -> tuple[int, Any]:
    tag = r.unpack("B")
    if tag == 0:
        return (0, r.i128())
    if tag == 1:
        return (1, r.unpack("Q"))
    if tag == 2:
        return (2, r.unpack("q"))
    if tag == 3:
        return (3, r.text())
    raise ValueError(f"snapshot holds an unknown key tag {tag}")


def _write_tracker(w: _Writer, t: _Tracker) -> None:
    if t.mode != "bounded":
        raise ValueError(_BOUNDED_ONLY)
    w.pack("B", t.hll.p)
    w.buf += bytes(t.hll.registers)
    ss = t.ss
    w.pack("IQQI", ss.capacity, ss.n, ss._clock, len(ss.counts))
    for key in sorted(ss.counts):
        _write_key(w, key)
        count, err = ss.counts[key]
        w.pack("QQQ", count, err, ss._seq[key])


def _read_tracker(r: _Reader) -> _Tracker:
    sk = _sketches()
    t = _Tracker("bounded")
    p = r.unpack("B")
    if not 4 <= p <= 18:
        raise ValueError("snapshot holds an invalid HLL precision")
    registers = list(r.take(1 << p))
    try:
        _check_registers(p, registers)
    except ValueError as exc:
        raise ValueError(f"snapshot: {exc}") from None
    t.hll = sk.HyperLogLog(p, registers)
    capacity, n, clock, length = (r.unpack(f) for f in "IQQI")
    if capacity == 0:
        raise ValueError("snapshot holds a SpaceSaving capacity of 0")
    if length > capacity:
        raise ValueError("snapshot holds more SpaceSaving entries than its capacity")
    ss = sk.SpaceSaving(capacity)
    for _ in range(length):
        key = _read_key(r)
        count, err, seq = (r.unpack("Q") for _ in range(3))
        ss.counts[key] = (count, err)
        ss._seq[key] = seq
    ss._clock, ss.n = clock, n
    t.ss = ss
    return t


def _write_kll(w: _Writer, kll: Any) -> None:
    w.pack("QQQI", kll.k, kll.n, kll._compactions, len(kll.levels))
    for level in kll.levels:
        w.pack("I", len(level))
        w.pack(f"{len(level)}d", *level)


def _read_kll(r: _Reader) -> Any:
    k, n, compactions, nlevels = (r.unpack(f) for f in "QQQI")
    levels = []
    for _ in range(nlevels):
        length = r.unpack("I")
        levels.append(list(struct.unpack(f"<{length}d", r.take(8 * length))))
    try:
        _check_kll(k, levels)
    except ValueError as exc:
        raise ValueError(f"snapshot: {exc}") from None
    return _sketches().KLL(k, levels or [[]], n, compactions)


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

    def snapshot(self) -> bytes:
        """The bounded-mode state as bytes (the Rust kernel's format); see ``from_snapshot``."""
        if self._mode != "bounded":
            raise ValueError(_BOUNDED_ONLY)
        w = _Writer()
        w.buf += _SNAP_MAGIC
        w.pack("BBQI", _SNAP_VERSION, 1, self._rows, len(self._cols))
        for col in self._cols:
            col.write_snapshot(w)
        return bytes(w.buf)

    @staticmethod
    def from_snapshot(schema: Any, data: bytes) -> ProfileState:
        r = _Reader(bytes(data))
        if r.take(4) != _SNAP_MAGIC:
            raise ValueError("not a profile snapshot")
        if r.unpack("B") != _SNAP_VERSION:
            raise ValueError("unsupported profile snapshot version")
        if r.unpack("B") != 1:
            raise ValueError(_BOUNDED_ONLY)
        state = ProfileState(schema, "bounded")
        state._rows = r.unpack("Q")
        if r.unpack("I") != len(state._cols):
            raise ValueError("snapshot column count differs from the schema")
        for col in state._cols:
            col.read_snapshot(r)
        if r.i != len(r.data):
            raise ValueError("snapshot has trailing bytes")
        return state
