"""Deterministic pairs of tables that hit every branch of the fidelity tiers (numpy + pyarrow only).

``tables()`` returns ``(real, synthetic)``: dicts of Arrow tables built from seeded numpy streams.
They cover numbers with nulls, integers, a two-component mixture (so the mixture fit picks more
than one Gaussian), booleans, categoricals with nulls (conditional profiles), text that is mostly
e-mails, ZIP codes and UUIDs (format preservation), long text (trigram similarity), timestamps
with and without nulls (gap distribution, autocorrelation), dates and decimals (read as text by
the tiers), an all-null column, a periodic series, a table of 12 rows (every minimum-size rule), a
150-row table (the bootstrap and noise outputs are recorded for tables of at most 200 rows), a
table with more rows than every sampling cap, and a synthetic table with a ``_shape_is_anomaly``
flag column. ``golden.py`` records the baseline's outputs for these in
``fixtures/expected_tiers.json``.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pyarrow as pa

BASE_NS = int(np.datetime64("2024-01-01T00:00:00", "ns").astype(np.int64))


def _stamps(rng: np.random.Generator, n: int, scale: float, nulls: float = 0.0) -> pa.Array:
    gaps = rng.exponential(scale, n) * 1e9
    ts = (BASE_NS + np.cumsum(gaps)).astype(np.int64)
    mask = rng.random(n) < nulls if nulls else None
    return pa.array(ts, type=pa.timestamp("ns"), mask=mask)


def _mixed(rng: np.random.Generator, n: int, shift: float, emails: float) -> pa.Table:
    seg = rng.choice(["retail", "wholesale", "online", "partner"], n, p=[0.5, 0.2, 0.2, 0.1])
    region = rng.choice(["north", "south", "east"], n)
    amount = np.exp(rng.normal(3.0 + shift, 0.6, n))
    two = np.where(rng.random(n) < 0.35, rng.normal(10, 1, n), rng.normal(40 + shift, 3, n))
    local = [f"user{i}" for i in rng.integers(0, 10**6, n)]
    email = [f"{u}@example.com" if rng.random() < emails else f"{u}.example.com" for u in local]
    t = np.arange(n, dtype=np.float64)
    return pa.table(
        {
            "id": pa.array(np.arange(1, n + 1, dtype=np.int64)),
            "amount": pa.array(amount, mask=rng.random(n) < 0.05),
            "qty": pa.array(rng.integers(1, 20, n)),
            "bimodal": pa.array(two),
            "flag": pa.array(rng.random(n) < 0.3 + shift / 10),
            "segment": pa.array(seg, mask=rng.random(n) < 0.03),
            "region": pa.array(region),
            "email": pa.array(email),
            "zip": pa.array([f"{z:05d}" for z in rng.integers(0, 99999, n)]),
            "code": pa.array([f"{rng.integers(0, 2**60):016x}" for _ in range(n)]),
            "created": _stamps(rng, n, 30.0 + 10 * shift),
            "updated": _stamps(rng, n, 45.0, nulls=0.05),
            "day": pa.array(
                [date(2024, 1, 1) + timedelta(days=int(d)) for d in rng.integers(0, 365, n)]
            ),
            "price": pa.array(
                [Decimal(int(v)) / 100 for v in rng.integers(100, 99999, n)],
                type=pa.decimal128(10, 2),
            ),
            "empty": pa.array([None] * n, type=pa.string()),
            "wave": pa.array(10 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 1, n)),
            "note": pa.array(
                [
                    " ".join(rng.choice(["alpha", "beta", "gamma", "delta", "omega"], 4))
                    for _ in range(n)
                ]
            ),
        }
    )


def _tiny(rng: np.random.Generator, shift: float) -> pa.Table:
    n = 12
    return pa.table(
        {
            "k": pa.array(np.arange(n, dtype=np.int64)),
            "v": pa.array(rng.normal(shift, 1, n)),
            "name": pa.array([f"name{i}" for i in range(n)]),
            "at": _stamps(rng, n, 100.0),
        }
    )


def _big(rng: np.random.Generator, n: int, shift: float) -> pa.Table:
    return pa.table(
        {
            "x": pa.array(rng.normal(shift, 1, n)),
            "n": pa.array(rng.integers(0, 50, n)),
            "grp": pa.array(rng.choice(["a", "b", "c"], n)),
            "label": pa.array([f"item-{v}" for v in rng.integers(0, 5000, n)]),
            "mail": pa.array([f"m{v}@host.net" for v in rng.integers(0, 3000, n)]),
        }
    )


def tables() -> tuple[dict[str, pa.Table], dict[str, pa.Table]]:
    """The reference tables and the synthetic ones (same names, nearby but different data)."""
    r, s = np.random.default_rng(11), np.random.default_rng(12)
    real = {
        "mixed": _mixed(r, 1200, 0.0, 0.9),
        "mini": _mixed(r, 150, 0.0, 0.9),
        "tiny": _tiny(r, 0.0),
        "big": _big(r, 5200, 0.0),
    }
    synth = {
        "mixed": _mixed(s, 1100, 0.3, 0.6),
        "mini": _mixed(s, 140, 0.3, 0.9),
        "tiny": _tiny(s, 0.5),
        "big": _big(s, 5400, 0.1),
    }
    flags = s.random(synth["mixed"].num_rows) < 0.12
    synth["mixed"] = synth["mixed"].append_column("_shape_is_anomaly", pa.array(flags))
    return real, synth
