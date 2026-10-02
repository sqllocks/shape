"""Shared by both sides of the chaos parity harness: the inputs, the tool-agnostic classifier
that reads a mutation off an (input, output) pair, and the canonical digest.

Imported from the Shape venv (``verify.py``) and from the baseline venv (``baseline_worker.py``),
so it uses only the standard library, numpy and pyarrow.
"""

from __future__ import annotations

import hashlib
import math
from typing import Any

import numpy as np
import pyarrow as pa

N_ROWS = 1000
JUNK = {"N/A", "null", "#REF!", "---", "TBD"}
LATIN = ("\xe9", "\xf1", "\xfc", "\xe4", "\xf6")
BOM = "﻿"
DST = {
    np.datetime64("2024-03-10T02:30:00", "us"),
    np.datetime64("2024-11-03T01:30:00", "us"),
    np.datetime64("2025-03-09T02:30:00", "us"),
    np.datetime64("2025-11-02T01:30:00", "us"),
}
TZ_HOURS = {-5, -6, -8, 1, 5, 8, 9}
FUTURE_FLOOR = np.datetime64("2031-01-01", "us")
POISON = [
    b'\n{"_poison": true, "value": NaN}\n',
    b"\n{incomplete json\n",
    b"\n\x00\x00\x00\n",
    b'\n{"nested": {"too": {"deep": {"for": {"parsers": "maybe"}}}}}\n',
    b"\n[]\n",
]
UTF8_BOM = b"\xef\xbb\xbf"


# ---------------------------------------------------------------------------------------------
# Inputs (fixed seed: both tools get the same data)
# ---------------------------------------------------------------------------------------------


def _timestamps(rng: np.random.Generator, n: int, start: str) -> np.ndarray:
    base = np.datetime64(start, "us") + (np.arange(n) * 7 * 60 * 10**6).astype("timedelta64[us]")
    return base + rng.integers(0, 10**6, size=n).astype("timedelta64[us]")


def make_frame(n: int = N_ROWS) -> pa.Table:
    rng = np.random.default_rng(20260101)
    return pa.table(
        {
            "id": pa.array(np.arange(1, n + 1), pa.int64()),
            "qty": pa.array(rng.integers(1, 20, n), pa.int32()),
            "unit_price": pa.array(np.round(rng.random(n) * 90 + 5, 2), pa.float64()),
            "total_amount": pa.array(np.round(rng.random(n) * 900 + 50, 2), pa.float64()),
            "name": pa.array([f"name{i}" for i in range(n)], pa.string()),
            "city": pa.array([("Oslo", "Lima", "Kyiv", "Pune")[i % 4] for i in range(n)]),
            "created_at": pa.array(_timestamps(rng, n, "2024-01-05T00:00:00"), pa.timestamp("us")),
            "updated_at": pa.array(_timestamps(rng, n, "2024-02-01T00:00:00"), pa.timestamp("us")),
        }
    )


def make_tables(n: int = N_ROWS) -> dict[str, pa.Table]:
    rng = np.random.default_rng(20260102)
    ids = pa.array(np.arange(1, n + 1), pa.int64())
    return {
        "customer": pa.table(
            {
                "customer_id": ids,
                "name": pa.array([f"c{i}" for i in range(n)]),
                "tier": pa.array(rng.integers(1, 4, n)),
            }
        ),
        "product": pa.table({"product_id": ids, "label": pa.array([f"p{i}" for i in range(n)])}),
        "orders": pa.table(
            {
                "order_id": ids,
                "customer_id": pa.array(rng.integers(1, n + 1, n), pa.int64()),
                "product_id": pa.array(rng.integers(1, n + 1, n), pa.int64()),
                "amount": pa.array(np.round(rng.random(n) * 500, 2)),
            }
        ),
    }


def make_file_bytes(rows: int = 600) -> bytes:
    lines = ["id,name,city,amount"]
    lines += [f"{i},name{i},Oslo,{i * 1.5:.2f}" for i in range(rows)]
    return ("\n".join(lines) + "\n").encode()


# ---------------------------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------------------------


def _is_null(v: Any) -> bool:
    return v is None or (isinstance(v, float) and math.isnan(v))


def _as_float(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return None


def same(old: Any, new: Any) -> bool:
    """Whether two cells hold the same value (a number equals its own text)."""
    if _is_null(old) or _is_null(new):
        return _is_null(old) and _is_null(new)
    if isinstance(old, str) and isinstance(new, str):
        return old == new
    fo, fn = _as_float(old), _as_float(new)
    if fo is not None and fn is not None and not isinstance(old, str | bytes):
        return fo == fn
    return bool(old == new)


def canon(v: Any) -> str:
    """One text per value: numbers (and text that reads as a number) by their float value,
    null by ``~``. Used for the informational identity check only."""
    if _is_null(v):
        return "~"
    f = _as_float(v)
    if f is not None and not isinstance(v, str):
        return "n:" + repr(f)
    if isinstance(v, str):
        g = _as_float(v)
        return "n:" + repr(g) if g is not None else "s:" + v
    return "o:" + str(v)


def digest(tables: dict[str, pa.Table] | pa.Table | bytes) -> str:
    h = hashlib.sha256()
    if isinstance(tables, bytes):
        h.update(tables)
        return h.hexdigest()
    items = tables if isinstance(tables, dict) else {"": tables}
    for name in sorted(items):
        t = items[name]
        h.update(f"{name}|{t.num_rows}|{t.column_names}".encode())
        for i in range(t.num_columns):
            for v in t.column(i).to_pylist():
                h.update(canon(v).encode())
                h.update(b"\x1f")
    return h.hexdigest()


def _event(
    kind: str, column: str | None, rows: int, n: int, rate: float | None = None
) -> dict[str, Any]:
    return {
        "kind": kind,
        "column": column,
        "rows": rows,
        "rate": rows / n if rate is None else rate,
    }


# ---------------------------------------------------------------------------------------------
# Classifiers: (before, after) -> [event]
# ---------------------------------------------------------------------------------------------


def _changed(old: list[Any], new: list[Any]) -> list[int]:
    return [i for i, (a, b) in enumerate(zip(old, new, strict=True)) if not same(a, b)]


def classify_value(before: pa.Table, after: pa.Table) -> list[dict[str, Any]]:
    n = before.num_rows
    events: list[dict[str, Any]] = []
    for i, name in enumerate(before.column_names):
        old, new = before.column(i).to_pylist(), after.column(i).to_pylist()
        numbers = [
            abs(float(v)) for v in old if _as_float(v) is not None and not isinstance(v, str)
        ]
        peak = max(numbers) if numbers else 0.0
        peak = peak or 1000.0
        found: dict[str, int] = {}
        for j in _changed(old, new):
            a, b = old[j], new[j]
            kind = "overlap"
            if _is_null(b):
                kind = "inject_nulls"
            elif isinstance(b, str) and b in JUNK:
                kind = "wrong_types"
            elif isinstance(b, str):
                base = a if isinstance(a, str) else str(a)
                if b == BOM + base or (b.startswith(base) and b[len(base) :] in LATIN):
                    kind = "encoding_issues"
            elif hasattr(b, "year") and hasattr(a, "year"):
                if np.datetime64(b, "us") >= FUTURE_FLOOR:
                    kind = "future_dates"
            elif _as_float(b) is not None and _as_float(a) is not None:
                fa, fb = float(a), float(b)
                if fa != 0 and fb == -fa:
                    kind = "negative_amounts"
                elif abs(fb) >= 99 * peak:
                    kind = "out_of_range"
            found[kind] = found.get(kind, 0) + 1
        events += [_event(k, name, c, n) for k, c in found.items()]
    return events


def classify_schema(before: pa.Table, after: pa.Table) -> list[dict[str, Any]]:
    n = before.num_rows
    b_names, a_names = before.column_names, after.column_names
    events: list[dict[str, Any]] = []
    b_left = [c for c in b_names if c not in a_names]
    a_left = [c for c in a_names if c not in b_names]
    for c in list(a_left):
        if c.startswith("_chaos_extra_"):
            events.append(_event("add_column", c, n, n))
            a_left.remove(c)
    for c in list(b_left):
        for d in list(a_left):
            same_data = _col_canon(before, c) == _col_canon(after, d)
            if same_data:
                events.append(_event("rename_column", c, n, n))
                b_left.remove(c)
                a_left.remove(d)
                break
    events += [_event("drop_column", c, n, n) for c in b_left]
    for c in b_names:
        if c in a_names and before.schema.field(c).type != after.schema.field(c).type:
            events.append(_event("retype_column", c, n, n))
    common_before = [c for c in b_names if c in a_names]
    common_after = [c for c in a_names if c in b_names]
    if common_before != common_after:
        events.append(_event("reorder", None, n, n))
    return events


def _col_canon(t: pa.Table, name: str) -> list[str]:
    return [canon(v) for v in t.column(name).to_pylist()]


def classify_temporal(before: pa.Table, after: pa.Table) -> list[dict[str, Any]]:
    n = before.num_rows
    events: list[dict[str, Any]] = []
    for i, name in enumerate(before.column_names):
        if not pa.types.is_timestamp(before.schema.field(i).type):
            continue
        old = np.array(before.column(i).to_numpy(zero_copy_only=False)).astype("datetime64[us]")
        new = np.array(after.column(i).to_numpy(zero_copy_only=False)).astype("datetime64[us]")
        existing = set(old.tolist())
        found: dict[str, int] = {}
        for j in np.nonzero(old != new)[0]:
            delta = (new[j] - old[j]).astype("timedelta64[us]").astype(np.int64)
            hours, rem = divmod(int(delta), 3600 * 10**6)
            kind = "overlap"
            if new[j] in DST:
                kind = "dst_boundary"
            elif new[j].tolist() in existing:
                kind = "out_of_order"
            elif (
                rem == 0
                and delta < 0
                and (-delta) % (24 * 3600 * 10**6) == 0
                and (1 <= (-delta) // (24 * 3600 * 10**6) <= 30)
            ):
                kind = "late_arrivals"
            elif rem == 0 and hours in TZ_HOURS:
                kind = "timezone_mismatch"
            found[kind] = found.get(kind, 0) + 1
        events += [_event(k, name, c, n) for k, c in found.items()]
    return events


def classify_volume(before: pa.Table, after: pa.Table) -> list[dict[str, Any]]:
    n0, n1 = before.num_rows, after.num_rows
    kind = "empty" if n1 == 0 else "single_row" if n1 == 1 else "spike" if n1 > n0 else "unknown"
    return [_event(kind, None, n1, n0, rate=n1 / n0)]


def classify_referential(
    before: dict[str, pa.Table], after: dict[str, pa.Table]
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for t, tb in before.items():
        n = tb.num_rows
        ta = after[t]
        for i, name in enumerate(tb.column_names):
            old, new = tb.column(i).to_pylist(), ta.column(i).to_pylist()
            idx = _changed(old, new)
            if not idx:
                continue
            fk = i > 0 and name.endswith("_id")
            orphan = all((_as_float(new[j]) or 0) >= 9_000_000 for j in idx)
            kind = "orphan_fks" if fk and orphan else "duplicate_pks" if i == 0 else "unknown"
            events.append(_event(kind, f"{t}.{name}", len(idx), n))
    return events


def classify_file(before: bytes, after: bytes) -> list[dict[str, Any]]:
    n, m = len(before), len(after)
    rate: float | None = None
    if m == 0:
        kind = "zero_byte"
    elif m < n and before.startswith(after):
        kind, rate = "truncate", m / n
    elif m == n:
        diff = [i for i in range(n) if before[i] != after[i]]
        cut = after.find(b"\x00")
        if not diff:
            kind = "unchanged"
        elif cut >= 0 and set(after[cut:]) == {0} and after[:cut] == before[:cut]:
            kind, rate = "partial_write", cut / n
        elif all(before[i] in b",\t|" for i in diff) and {before[i] for i in diff} == {
            before[diff[0]]
        }:
            kind = "wrong_delimiter"
        else:
            kind, rate = "corrupt_encoding", len(diff) / n
    elif m > n and after.endswith(before) and 8 <= m - n < 64:
        kind, rate = "garbage_header", float(m - n)
    elif m > n and after == UTF8_BOM + before:
        kind = "bom_injection"
    else:
        kind = "unknown"
        grown = m - n
        if grown > 0:
            lead = next((i for i in range(n) if before[i] != after[i]), n)
            for payload in [*POISON, UTF8_BOM]:
                if len(payload) != grown:
                    continue
                for pos in range(lead, max(-1, lead - len(payload) - 1), -1):
                    if before[:pos] + payload + before[pos:] == after:
                        kind = "bom_injection" if payload == UTF8_BOM else "invalid_json_poison"
                        break
                if kind != "unknown":
                    break
    return [_event(kind, None, abs(m - n), n, rate=rate if rate is not None else 0.0)]


def make_rng(seed: int) -> np.random.Generator:
    return np.random.default_rng(seed)
