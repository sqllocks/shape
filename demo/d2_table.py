"""The D2 profiling table: 20 mixed-type columns, deterministic for a seed.

Shape's own code (numpy + pyarrow only). ``demo/make_data.py`` writes it as ``day1/d2.parquet``
and ``demo/contracts/d2.json`` checks it. The table mixes a primary key, integers, normal,
uniform, exponential and log-normal floats, decimals stored as strings, phone numbers, emails,
ZIP+4 codes, UUIDs, IPv4 addresses, currency codes, low-cardinality enums, dates, timestamps,
booleans and nulls at rates from 0.5% to 20%.
"""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc

STATUS = ["active", "inactive", "pending", "suspended", "closed"]
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "SEK", "NZD"]
DOMAINS = ["example.com", "mail.org", "corp.net", "test.io"]


def _istr(a, width: int | None = None) -> pa.Array:
    s = pc.cast(pa.array(np.asarray(a, dtype=np.int64)), pa.string())
    if width:
        s = pc.utf8_lpad(s, width=width, padding="0")
    return s


def _join(*parts) -> pa.Array:
    parts = [p if isinstance(p, (pa.Array, pa.ChunkedArray)) else pa.scalar(p) for p in parts]
    return pc.binary_join_element_wise(*parts, "")


def _pick(rng, choices, n, p=None) -> pa.Array:
    idx = rng.choice(len(choices), size=n, p=p)
    return pc.take(pa.array(choices), pa.array(idx))


def _money(cents) -> pa.Array:
    cents = np.asarray(cents, dtype=np.int64)
    return _join(_istr(cents // 100), ".", _istr(cents % 100, 2))


def _with_nulls(arr, rng, rate: float):
    arr = arr if isinstance(arr, (pa.Array, pa.ChunkedArray)) else pa.array(arr)
    if rate <= 0:
        return arr
    mask = rng.random(len(arr)) < rate
    return pc.if_else(pa.array(mask), pa.scalar(None, arr.type), arr)


def _dates(rng, n, start="2018-01-01", days=2000) -> pa.Array:
    base = np.datetime64(start, "D")
    d = base + rng.integers(0, days, n).astype("timedelta64[D]")
    return pa.array(d.astype("datetime64[D]"), type=pa.date32())


def _timestamps(rng, n, start="2020-01-01", seconds=3 * 365 * 86400) -> pa.Array:
    base = np.datetime64(start, "s")
    # business-hours skew so hour histograms are non-trivial
    day = rng.integers(0, seconds // 86400, n).astype("timedelta64[D]")
    hour = np.clip(rng.normal(13, 4, n), 0, 23.99).astype(np.int64)
    sec = hour * 3600 + rng.integers(0, 3600, n)
    t = base + day + sec.astype("timedelta64[s]")
    return pa.array(t, type=pa.timestamp("s"))


def _uuid(rng, n) -> pa.Array:
    hexd = np.frombuffer(b"0123456789abcdef", dtype="S1")
    r = rng.integers(0, 16, size=(n, 32), dtype=np.int8)
    chars = hexd[r]  # (n,32) S1
    groups = []
    for a, b in ((0, 8), (8, 12), (12, 16), (16, 20), (20, 32)):
        g = np.ascontiguousarray(chars[:, a:b]).view(f"S{b - a}").ravel()
        groups.append(pa.array(g.astype(str)))
    return pc.binary_join_element_wise(*groups, "-")


def d2_table(n: int, seed: int = 2) -> pa.Table:
    rng = np.random.default_rng(seed)
    cents = rng.integers(0, 5_000_000, n)
    zip5 = rng.integers(0, 100_000, n)
    area = rng.integers(200, 999, n)
    cols = {
        "customer_id": pa.array(np.arange(1, n + 1)),
        "age": pa.array(rng.integers(18, 91, n)),
        "income": _with_nulls(np.round(rng.normal(60_000, 15_000, n), 2), rng, 0.02),
        "balance": _money(cents),  # decimal text, so a string column
        "phone": _join(
            "(",
            _istr(area),
            ") ",
            _istr(rng.integers(100, 999, n)),
            "-",
            _istr(rng.integers(0, 10_000, n), 4),
        ),
        "email": _with_nulls(
            _join("user", _istr(rng.integers(0, n * 10, n)), "@", _pick(rng, DOMAINS, n)), rng, 0.01
        ),
        "zip4": _join(_istr(zip5, 5), "-", _istr(rng.integers(0, 10_000, n), 4)),
        "status": _with_nulls(_pick(rng, STATUS, n, p=[0.6, 0.2, 0.1, 0.07, 0.03]), rng, 0.005),
        "currency": _pick(rng, CURRENCIES, n),
        "signup_date": _dates(rng, n),
        "last_login": _with_nulls(_timestamps(rng, n), rng, 0.05),
        "is_active": pa.array(rng.random(n) < 0.8),
        "has_promo": _with_nulls(pa.array(rng.random(n) < 0.3), rng, 0.10),
        "order_value": pa.array(np.round(rng.lognormal(3.5, 0.9, n), 2)),
        "store_id": pa.array(rng.integers(1, 501, n)),
        "score": pa.array(np.round(rng.uniform(0, 100, n), 4)),
        "wait_time": pa.array(np.round(rng.exponential(12.0, n), 3)),
        "qty": _with_nulls(pa.array(rng.integers(1, 20, n)), rng, 0.20),
        "session_uuid": _uuid(rng, n),
        "ip_address": _join(
            _istr(rng.integers(1, 255, n)),
            ".",
            _istr(rng.integers(0, 256, n)),
            ".",
            _istr(rng.integers(0, 256, n)),
            ".",
            _istr(rng.integers(1, 255, n)),
        ),
    }
    return pa.table(cols)
