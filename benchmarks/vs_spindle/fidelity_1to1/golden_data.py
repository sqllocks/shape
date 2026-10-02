"""Deterministic reference/synthetic table pairs that exercise every branch of the scoring.

Standard library, numpy and pyarrow only: it is imported by ``golden.py`` (baseline venv), which
records the baseline comparator's scores for these pairs in ``fixtures/expected_scores.json``,
and by ``tests/benchmarks/test_fidelity_1to1.py`` (Shape's venv), which checks Shape's scores
against that file.
"""

from __future__ import annotations

import datetime as dt

import numpy as np
import pyarrow as pa

N = 600


def _dates(rng: np.random.Generator, n: int, shift_days: int = 0) -> list[dt.datetime]:
    base = dt.datetime(2024, 1, 1)
    return [
        base + dt.timedelta(days=int(d) + shift_days, seconds=int(s))
        for d, s in zip(rng.integers(0, 365, n), rng.integers(0, 86400, n), strict=True)
    ]


def tables() -> tuple[dict[str, pa.Table], dict[str, pa.Table]]:
    rng = np.random.default_rng(20260930)
    cats = np.array(["red", "green", "blue", "amber", "violet"])
    real_c = rng.choice(cats, N, p=[0.4, 0.3, 0.15, 0.1, 0.05])
    same_c = rng.choice(cats, N, p=[0.4, 0.3, 0.15, 0.1, 0.05])
    mild_c = rng.choice(cats, N, p=[0.3, 0.3, 0.2, 0.1, 0.1])
    far_c = rng.choice(cats, N, p=[0.05, 0.05, 0.1, 0.3, 0.5])
    extra_c = rng.choice(np.append(cats, "teal"), N)
    nums = rng.normal(100, 15, N)
    nulls = np.where(rng.random(N) < 0.1, None, rng.normal(5, 1, N))
    z = "00000"
    real = pa.table(
        {
            "float_same": nums,
            "float_shifted": rng.normal(100, 15, N),
            "float_wide": rng.normal(100, 15, N),
            "int_ids": np.arange(N),
            "int_skew": rng.poisson(3, N),
            "float_nulls": pa.array(list(nulls), pa.float64()),
            "cat_same": real_c,
            "cat_mild": real_c,
            "cat_far": real_c,
            "cat_extra": real_c,
            "flag": rng.random(N) < 0.3,
            "unique_text": [f"user-{i}" for i in range(N)],
            "numeric_text": [f"{z[: i % 3]}{i % 50}" for i in range(N)],
            "date_us": pa.array(_dates(rng, N), pa.timestamp("us")),
            "date_ns": pa.array(_dates(rng, N), pa.timestamp("ns")),
            "date_text": [d.strftime("%Y-%m-%d") for d in _dates(rng, N)],
            "datetime_text": [d.strftime("%Y-%m-%dT%H:%M:%S") for d in _dates(rng, N)],
            "date_only": pa.array([d.date() for d in _dates(rng, N)], pa.date32()),
            "all_null": pa.array([None] * N, pa.string()),
            "constant": ["same"] * N,
            "decimal": pa.array([f"{i % 97}.25" for i in range(N)]).cast(pa.decimal128(8, 2)),
        }
    )
    synth = pa.table(
        {
            "float_same": rng.normal(100, 15, N),
            "float_shifted": rng.normal(112, 15, N),
            "float_wide": rng.normal(100, 40, N),
            "int_ids": np.arange(N) + 1,
            "int_skew": rng.poisson(6, N),
            "float_nulls": pa.array(
                list(np.where(rng.random(N) < 0.3, None, rng.normal(5, 1, N))), pa.float64()
            ),
            "cat_same": same_c,
            "cat_mild": mild_c,
            "cat_far": far_c,
            "cat_extra": extra_c,
            "flag": rng.random(N) < 0.6,
            "unique_text": [f"user-{i + 300}" for i in range(N)],
            "numeric_text": [f"{(i * 7) % 60}" for i in range(N)],
            "date_us": pa.array(_dates(rng, N, 20), pa.timestamp("us")),
            "date_ns": pa.array(_dates(rng, N), pa.timestamp("ns")),
            "date_text": [d.strftime("%Y-%m-%d") for d in _dates(rng, N, 5)],
            "datetime_text": [d.strftime("%Y-%m-%dT%H:%M:%S") for d in _dates(rng, N)],
            "date_only": pa.array([d.date() for d in _dates(rng, N, 3)], pa.date32()),
            "all_null": pa.array([None] * N, pa.string()),
            "constant": ["other"] * N,
            "decimal": pa.array([f"{(i * 3) % 97}.50" for i in range(N)]).cast(pa.decimal128(8, 2)),
        }
    )
    tiny_real = pa.table(
        {
            "few_numbers": [1.0, 2.0, 3.0, 4.0],
            "one_number": [7.0, None, None, None],
            "few_text": ["a", "b", "a", "b"],
            "kind_mismatch": [1, 2, 3, 4],
        }
    )
    tiny_synth = pa.table(
        {
            "few_numbers": [1.5, 2.5, 3.5, 4.5],
            "one_number": [8.0, None, None, None],
            "few_text": ["a", "a", "a", "b"],
            "kind_mismatch": ["1", "2", "3", "x"],
        }
    )
    return {"wide": real, "tiny": tiny_real}, {"wide": synth, "tiny": tiny_synth}
