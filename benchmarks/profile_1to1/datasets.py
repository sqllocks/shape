"""Deterministic benchmark datasets for the Spindle-profiler 1:1 port.

Writes CSV + Parquet files to /tmp/claude-0/profile_data/ (override with
PROFILE_DATA_DIR).  Uses numpy + pyarrow only, seeded, so every run produces
byte-identical files.

    python datasets.py            # all datasets
    python datasets.py D2 MT      # only some

Datasets
  D1  200k x 6    the pre-existing "easy" shape (id, age, zip, state, email, amount)
  D2  1M   x 20   mixed: PK, ints, normal/uniform/expon/lognormal floats,
                  decimals (float in CSV, *string* in Parquet), phone, email,
                  zip+4, uuid, ipv4, currency codes, low-card enums, dates,
                  timestamps, booleans, nulls at 0.5%..20%
  D3  5M   x 10   long table (CSV + Parquet)
  D4  100k x 200  wide table (cycling column archetypes)
  MT  multi-table (customer / product / orders) for profile_dataset() FK detection
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.csv as pacsv
import pyarrow.parquet as pq

OUT = Path(os.environ.get("PROFILE_DATA_DIR", "/tmp/claude-0/profile_data"))
D1_SOURCE = Path("/tmp/claude-0/-home-user-shape/f479a069-6c64-511a-988a-3f7ac0358a89/scratchpad/c.csv")

STATES = ["CA", "TX", "NY", "FL", "IL", "PA", "OH", "GA", "NC", "MI", "NJ", "VA", "WA", "AZ", "MA"]
STATUS = ["active", "inactive", "pending", "suspended", "closed"]
CURRENCIES = ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF", "CNY", "SEK", "NZD"]
DOMAINS = ["example.com", "mail.org", "corp.net", "test.io"]


# ---------------------------------------------------------------------------
# vectorised string helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------

def _csv_table(t: pa.Table) -> pa.Table:
    """Render every column to the exact text we want in the CSV."""
    cols = {}
    for name, col in zip(t.column_names, t.columns):
        typ = col.type
        if pa.types.is_boolean(typ):
            col = pc.if_else(col, "True", "False")
        elif pa.types.is_timestamp(typ):
            col = pc.strftime(col, format="%Y-%m-%d %H:%M:%S")
        elif pa.types.is_date(typ):
            col = pc.strftime(col, format="%Y-%m-%d")
        cols[name] = col
    return pa.table(cols)


def write(t: pa.Table, stem: str, csv=True, parquet=True, parquet_table: pa.Table | None = None):
    OUT.mkdir(parents=True, exist_ok=True)
    if csv:
        pacsv.write_csv(_csv_table(t), OUT / f"{stem}.csv",
                        write_options=pacsv.WriteOptions(quoting_style="none"))
    if parquet:
        pq.write_table(parquet_table if parquet_table is not None else t, OUT / f"{stem}.parquet")
    print(f"wrote {stem}: {t.num_rows:,} x {t.num_columns}")


# ---------------------------------------------------------------------------
# datasets
# ---------------------------------------------------------------------------

def d1():
    if D1_SOURCE.exists():
        OUT.mkdir(parents=True, exist_ok=True)
        (OUT / "d1.csv").write_bytes(D1_SOURCE.read_bytes())
        t = pacsv.read_csv(OUT / "d1.csv")
        pq.write_table(t, OUT / "d1.parquet")
        print(f"wrote d1 (copied): {t.num_rows:,} x {t.num_columns}")
        return
    # regenerate the same shape if the scratch file is gone
    rng = np.random.default_rng(1)
    n = 200_000
    t = pa.table({
        "id": np.arange(n),
        "age": rng.integers(18, 90, n),
        "zip": rng.integers(10000, 99999, n),
        "state": _pick(rng, STATES, n),
        "email": _join("u", _istr(np.arange(n)), "@x.com"),
        "amount": np.round(rng.uniform(1, 1000, n), 2),
    })
    write(t, "d1")


def _d2_table(n: int, seed: int = 2):
    rng = np.random.default_rng(seed)
    cents = rng.integers(0, 5_000_000, n)
    zip5 = rng.integers(0, 100_000, n)
    area = rng.integers(200, 999, n)
    cols = {
        "customer_id": pa.array(np.arange(1, n + 1)),
        "age": pa.array(rng.integers(18, 91, n)),
        "income": _with_nulls(np.round(rng.normal(60_000, 15_000, n), 2), rng, 0.02),
        "balance": _money(cents),  # float in CSV, string column in Parquet
        "phone": _join("(", _istr(area), ") ", _istr(rng.integers(100, 999, n)), "-", _istr(rng.integers(0, 10_000, n), 4)),
        "email": _with_nulls(_join("user", _istr(rng.integers(0, n * 10, n)), "@", _pick(rng, DOMAINS, n)), rng, 0.01),
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
        "ip_address": _join(_istr(rng.integers(1, 255, n)), ".", _istr(rng.integers(0, 256, n)), ".",
                            _istr(rng.integers(0, 256, n)), ".", _istr(rng.integers(1, 255, n))),
    }
    return pa.table(cols)


def d2():
    t = _d2_table(1_000_000)
    write(t, "d2")


def d3():
    n = 5_000_000
    rng = np.random.default_rng(3)
    t = pa.table({
        "event_id": pa.array(np.arange(n)),
        "user_ref": pa.array(rng.integers(1, 200_000, n)),
        "latency_ms": pa.array(np.round(rng.normal(250, 40, n), 3)),
        "bytes": pa.array(np.round(rng.lognormal(8, 1.2, n), 1)),
        "event_type": _pick(rng, ["view", "click", "add_to_cart", "purchase", "logout", "login"], n,
                            p=[0.5, 0.25, 0.1, 0.05, 0.05, 0.05]),
        "event_date": _dates(rng, n, "2022-01-01", 900),
        "event_ts": _timestamps(rng, n),
        "is_mobile": pa.array(rng.random(n) < 0.55),
        "email": _join("u", _istr(rng.integers(0, 1_000_000, n)), "@", _pick(rng, DOMAINS, n)),
        "discount": _with_nulls(pa.array(np.round(rng.uniform(0, 0.5, n), 4)), rng, 0.3),
    })
    write(t, "d3")


def d4():
    n, k = 100_000, 200
    rng = np.random.default_rng(4)
    cols = {"row_id": pa.array(np.arange(n))}
    kinds = ["int_small", "normal", "uniform", "expon", "lognormal", "enum_str", "date", "bool",
             "int_null", "float_null"]
    for j in range(1, k):
        kind = kinds[j % len(kinds)]
        name = f"c{j:03d}_{kind}"
        if kind == "int_small":
            a = pa.array(rng.integers(0, 50 + j, n))
        elif kind == "normal":
            a = pa.array(np.round(rng.normal(j, 1 + j / 10, n), 4))
        elif kind == "uniform":
            a = pa.array(np.round(rng.uniform(-j, j, n), 4))
        elif kind == "expon":
            a = pa.array(np.round(rng.exponential(1 + j, n), 4))
        elif kind == "lognormal":
            a = pa.array(np.round(rng.lognormal(1, 0.5, n), 4))
        elif kind == "enum_str":
            a = _pick(rng, [f"cat_{i}" for i in range(3 + j % 17)], n)
        elif kind == "date":
            a = _dates(rng, n, "2015-06-01", 3000)
        elif kind == "bool":
            a = pa.array(rng.random(n) < 0.5)
        elif kind == "int_null":
            a = _with_nulls(pa.array(rng.integers(0, 1000, n)), rng, 0.05 + (j % 7) / 20)
        else:
            a = _with_nulls(pa.array(np.round(rng.normal(0, 1, n), 5)), rng, 0.02 + (j % 5) / 25)
        cols[name] = a
    write(pa.table(cols), "d4")


def mt():
    rng = np.random.default_rng(5)
    nc, npr, no = 20_000, 800, 200_000
    customer = pa.table({
        "customer_id": pa.array(np.arange(1, nc + 1)),
        "name": _join("Customer ", _istr(np.arange(1, nc + 1))),
        "email": _join("c", _istr(np.arange(1, nc + 1)), "@", _pick(rng, DOMAINS, nc)),
        "segment": _pick(rng, ["retail", "smb", "enterprise"], nc, p=[0.7, 0.25, 0.05]),
        "created": _dates(rng, nc, "2016-01-01", 2500),
    })
    product = pa.table({
        "product_id": pa.array(np.arange(1, npr + 1)),
        "sku": _join("SKU-", _istr(np.arange(1, npr + 1), 6)),
        "price": pa.array(np.round(rng.lognormal(3, 0.7, npr), 2)),
        "category": _pick(rng, ["toys", "books", "garden", "tools", "food"], npr),
    })
    cust = rng.integers(1, nc + 1, no)
    cust[rng.random(no) < 0.03] += nc  # 3% orphans -> overlap still >= 0.9
    orders = pa.table({
        "order_id": pa.array(np.arange(1, no + 1)),
        "customer_id": _with_nulls(pa.array(cust), rng, 0.02),
        "product_id": pa.array(rng.integers(1, npr + 1, no)),
        "quantity": pa.array(rng.integers(1, 10, no)),
        "amount": pa.array(np.round(rng.gamma(2.0, 30.0, no), 2)),
        "order_ts": _timestamps(rng, no),
    })
    sub = OUT / "mt"
    sub.mkdir(parents=True, exist_ok=True)
    for name, t in (("customer", customer), ("product", product), ("orders", orders)):
        pacsv.write_csv(_csv_table(t), sub / f"{name}.csv",
                        write_options=pacsv.WriteOptions(quoting_style="none"))
        pq.write_table(t, sub / f"{name}.parquet")
    print("wrote mt/{customer,product,orders}")


ALL = {"D1": d1, "D2": d2, "D3": d3, "D4": d4, "MT": mt}

if __name__ == "__main__":
    which = sys.argv[1:] or list(ALL)
    for w in which:
        ALL[w.upper()]()
