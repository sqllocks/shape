"""Builders shared by the fidelity-tier tests."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pyarrow as pa


def stamps(rng: np.random.Generator, n: int, scale: float = 30.0, nulls: float = 0.0) -> pa.Array:
    base = int(np.datetime64("2024-01-01", "ns").astype(np.int64))
    ts = (base + np.cumsum(rng.exponential(scale, n) * 1e9)).astype(np.int64)
    mask = rng.random(n) < nulls if nulls else None
    return pa.array(ts, type=pa.timestamp("ns"), mask=mask)


def make_table(
    seed: int = 0, n: int = 800, shift: float = 0.0, email_rate: float = 0.9
) -> pa.Table:
    rng = np.random.default_rng(seed)
    seg = rng.choice(["retail", "online", "partner"], n, p=[0.5, 0.3, 0.2])
    bimodal = np.where(rng.random(n) < 0.4, rng.normal(0, 1, n), rng.normal(8 + shift, 1, n))
    return pa.table(
        {
            "id": pa.array(np.arange(n, dtype=np.int64)),
            "amount": pa.array(rng.normal(100 + shift, 15, n), mask=rng.random(n) < 0.04),
            "bimodal": pa.array(bimodal),
            "flag": pa.array(rng.random(n) < 0.3),
            "segment": pa.array(seg, mask=rng.random(n) < 0.02),
            "email": pa.array(
                [f"u{i}@x.com" if rng.random() < email_rate else f"u{i}" for i in range(n)]
            ),
            "at": stamps(rng, n),
            "day": pa.array(
                [date(2024, 1, 1) + timedelta(days=int(d)) for d in rng.integers(0, 90, n)]
            ),
            "wave": pa.array(5 * np.sin(2 * np.pi * np.arange(n) / 20) + rng.normal(0, 0.5, n)),
        }
    )
