"""P4-08: profile -> generate -> profile, and the plan.

Acceptance: profiling what ``generate`` makes from a profile gives the same profile, for every field
the plan calls ``preserved``, within the tolerances below, and every field the plan calls
``approximate`` is within a looser tolerance. The profiler's T-22 tolerances (1e-9 on a mean, 1e-6
on a fitted parameter) compare two profilers on the *same* data; a regenerated sample differs by
sampling error, so a statistic is compared to its standard error (five of them), and the fields that
describe structure (type, key flags, pattern, which values an enum has) must be equal.

The test is tied to the plan: it checks what the plan claims, so a field cannot be reported as
preserved without being measured.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest
from pyarrow import csv

import shape
from shape.generation.fit import COLUMN_FIELDS, fit_schema

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "benchmarks" / "vs_refengine" / "profile_1to1"))
import datasets  # type: ignore[import-not-found]  # noqa: E402

N = 60_000
PERCENTILES = ("p1", "p5", "p10", "p25", "p50", "p75", "p90", "p95", "p99")


def tvd(a: list[float], b: list[float]) -> float:
    return 0.5 * sum(abs(x - y) for x, y in zip(a, b, strict=True))


def _tagged(value: Any) -> Any:
    return value[1] if isinstance(value, list) else value


def check_field(field: str, orig: dict[str, Any], regen: dict[str, Any], n: int) -> str | None:
    """``None`` if ``regen`` agrees with ``orig`` on ``field`` within the tolerance, else why."""
    a, b = orig[field], regen[field]
    if field in ("name", "dtype", "is_primary_key", "is_foreign_key", "fk_ref_table", "pattern"):
        return None if a == b else f"{a!r} != {b!r}"
    if field in ("mean", "std", "quantiles"):
        return check_approximate(field, orig, regen, n)
    if field == "outlier_rate":
        return None if abs((a or 0.0) - (b or 0.0)) <= 0.01 else f"{a} vs {b}"
    if field == "string_length":
        return None if a == b else f"{a!r} != {b!r}"
    if field in ("is_enum", "is_unique", "cardinality", "cardinality_ratio"):
        return None if a == b else f"{a!r} != {b!r}"
    if field in ("null_count", "null_rate"):
        rate = orig["null_rate"] or 0.0
        gap = abs((regen["null_rate"] or 0.0) - rate) if field == "null_rate" else None
        if gap is None:
            gap = abs(regen["null_count"] - rate * n) / n
        return None if gap <= 5 * math.sqrt(rate * (1 - rate) / n) + 1e-3 else f"off by {gap:.4f}"
    if field in ("value_counts_ext", "enum_values"):
        if set(a) != set(b):
            return f"values differ: {sorted(set(a) ^ set(b))[:5]}"
        worst = max(abs(a[k] - b[k]) - 5 * math.sqrt(a[k] * (1 - a[k]) / n) for k in a)
        return None if worst <= 1e-3 else f"a weight is off by more than 5 sigma ({worst:.4f})"
    if field == "value_counts_ext_order":
        return None
    if field in ("min_value", "max_value"):
        return None if a == b else f"{a!r} != {b!r}"
    if field in ("hour_histogram", "dow_histogram"):
        return None if tvd(a, b) <= 0.02 else f"TVD {tvd(a, b):.4f}"
    if field == "temporal_histogram":
        t = tvd(a["month_weights"], b["month_weights"])
        return None if t <= 0.02 else f"month TVD {t:.4f}"
    raise AssertionError(f"no check for {field}")


def _quantile_gaps(
    want: dict[str, float], got: dict[str, float], n: int, std: float
) -> list[tuple[str, str]]:
    """Quantiles that differ by more than five standard errors of the difference of two sample
    quantiles (the density at the quantile is read off the neighbouring quantiles), plus 3% of a
    standard deviation for the parametric fit."""
    keys = sorted(want, key=lambda k: float(k[1:].replace("_", ".")))
    probs = [float(k[1:].replace("_", ".")) / 100 for k in keys]
    out = []
    for i, k in enumerate(keys):
        lo, hi = max(i - 1, 0), min(i + 1, len(keys) - 1)
        spread = want[keys[hi]] - want[keys[lo]]
        density = (probs[hi] - probs[lo]) / spread if spread > 0 else math.inf
        se = math.sqrt(probs[i] * (1 - probs[i]) / n) / density if density < math.inf else 0.0
        se *= math.sqrt(2)  # the profile and the regenerated data are two samples
        if abs(got[k] - want[k]) > 5 * se + 0.03 * std:
            out.append((k, f"{got[k]} vs {want[k]} (5 SE = {5 * se:.3g})"))
    return out


def check_approximate(
    field: str, orig: dict[str, Any], regen: dict[str, Any], n: int
) -> str | None:
    """The looser check for a numeric statistic the plan calls approximate."""
    std = orig["std"] or 0.0
    if field == "mean":
        return (
            None
            if abs(regen["mean"] - orig["mean"]) <= 5 * std / math.sqrt(n) + 0.02 * std
            else "mean"
        )
    if field == "std":
        return None if abs(regen["std"] - std) <= 0.05 * std else f"std {regen['std']} vs {std}"
    if field == "quantiles":
        for p, why in _quantile_gaps(orig["quantiles"], regen["quantiles"], n, std):
            return f"{p}: {why}"
    return None


def roundtrip(table: pa.Table, name: str, rows: int | None = None) -> tuple[Any, dict, dict]:
    profile = shape.profile(table, name=name)
    fitted = fit_schema(profile, rows=rows)
    from shape.generation.engine import Engine

    out = Engine(fitted.schema, scale="profile", seed=21).generate().tables[name]
    return fitted, profile.to_dict(), shape.profile(out, name=name).to_dict()


@pytest.fixture(scope="module")
def d2_roundtrip(tmp_path_factory: pytest.TempPathFactory) -> tuple[Any, dict, dict]:
    path = tmp_path_factory.mktemp("d2") / "d2.csv"
    csv.write_csv(datasets._csv_table(datasets._d2_table(N)), str(path))
    profile = shape.profile(str(path))
    fitted = fit_schema(profile)
    from shape.generation.engine import Engine

    out = Engine(fitted.schema, scale="profile", seed=21).generate().tables["d2"]
    return fitted, profile.to_dict(), shape.profile(out, name="d2").to_dict()


def test_every_preserved_field_of_d2_is_measured_equal(d2_roundtrip: Any) -> None:
    fitted, orig, regen = d2_roundtrip
    problems: list[str] = []
    checked = 0
    for item in fitted.plan.items:
        parts = item.evidence.split(".")
        if len(parts) != 3 or parts[1] not in orig["columns"] or parts[2] not in COLUMN_FIELDS:
            continue
        _, col, field = parts
        if item.status == "preserved":
            if field in ("fit_score",):
                continue
            why = check_field(field, orig["columns"][col], regen["columns"][col], N)
            checked += 1
        elif item.status == "approximate" and field in ("mean", "std", "quantiles"):
            why = check_approximate(field, orig["columns"][col], regen["columns"][col], N)
            checked += 1
        else:
            continue
        if why:
            problems.append(f"{item.evidence} [{item.status}]: {why}")
    assert checked > 150  # the claims that were measured
    assert problems == []


def test_the_plan_covers_every_field_of_every_column(d2_roundtrip: Any) -> None:
    fitted, orig, _ = d2_roundtrip
    named = {i.evidence for i in fitted.plan.items}
    for col, doc in orig["columns"].items():
        for field in doc:
            if doc[field] is None or field in ("value_counts_ext_order",):
                continue
            assert field in COLUMN_FIELDS, f"the profile has a new field {field}: plan it"
            assert f"d2.{col}.{field}" in named, f"d2.{col}.{field} is not in the plan"
    assert {"d2.row_count", "d2.primary_key", "dataset.missingness_joint"} <= named


def test_the_plan_flags_what_is_not_modelled(d2_roundtrip: Any) -> None:
    fitted, _, _ = d2_roundtrip
    flagged = {i.evidence for i in fitted.plan.by_status("not_modelled")}
    # free text, patterns and uuids: the values are synthetic
    assert "d2.email.cardinality" in flagged and "d2.phone.string_length" in flagged
    assert "d2.session_uuid.min_value" in flagged
    # a goodness of fit and the dependence between nulls are measured, not generated
    assert "d2.income.fit_score" in flagged and "dataset.missingness_joint" in flagged
    assert fitted.plan.executable
    assert {"preserved", "approximate", "not_modelled"} <= set(fitted.plan.counts())


def test_a_numeric_enum_with_nulls_keeps_its_type_and_weights(d2_roundtrip: Any) -> None:
    _, orig, regen = d2_roundtrip
    for col in ("age", "qty", "store_id", "has_promo", "is_active", "status"):
        assert regen["columns"][col]["dtype"] == orig["columns"][col]["dtype"], col
    assert regen["columns"]["qty"]["null_rate"] == pytest.approx(0.2, abs=0.01)


def test_correlated_columns_with_missing_values() -> None:
    rng = np.random.default_rng(3)
    n = 80_000
    z = rng.multivariate_normal([0, 0, 0], [[1, 0.6, -0.4], [0.6, 1, 0.1], [-0.4, 0.1, 1]], n)
    table = pa.table(
        {
            "id": np.arange(1, n + 1),
            "x": np.round(
                np.exp(3 + 0.9 * z[:, 0]), 2
            ),  # log-normal: Pearson is not the copula's rho
            "y": pa.array(np.round(50 + 10 * z[:, 1], 2), mask=rng.random(n) < 0.1),
            "w": np.round(0.5 * (1 + np.vectorize(math.erf)(z[:, 2] / math.sqrt(2))) * 100, 3),
        }
    )
    fitted, orig, regen = roundtrip(table, "t")
    for a, b in (("x", "y"), ("x", "w"), ("y", "w")):
        want = orig["correlation_matrix"][a][b]
        got = regen["correlation_matrix"][a][b]
        assert abs(got - want) < 0.02, (a, b, want, got)
    assert regen["columns"]["y"]["null_rate"] == pytest.approx(0.1, abs=0.01)
    items = {i.evidence: i for i in fitted.plan.items}
    assert items["t.correlation_matrix[x,y]"].status == "approximate"
    assert "Gaussian copula" in items["t.correlation_matrix[x,y]"].reason


def test_weak_and_key_correlations_are_flagged_not_modelled_or_independent() -> None:
    rng = np.random.default_rng(4)
    n = 5_000
    x = rng.normal(size=n)
    table = pa.table(
        {
            "id": np.arange(n),
            "a": x,
            "b": x * 0.9 + rng.normal(size=n) * 0.1,
            "store_id": np.round(x * 50 + 500),  # named like a key: never reordered
            "c": rng.normal(size=n),
        }
    )
    fitted = fit_schema(shape.profile(table, name="t"))
    items = {i.evidence: i for i in fitted.plan.items}
    assert items["t.correlation_matrix[a,store_id]"].status == "not_modelled"
    assert items["t.correlation_matrix[a,c]"].reason.startswith("|r|")  # independent
    assert items["t.correlation_matrix[a,b]"].status == "approximate"


def test_seasonality_and_date_columns() -> None:
    rng = np.random.default_rng(5)
    n = 100_000
    start = np.datetime64("2021-01-01")
    day = rng.choice(730, n, p=_season(730))
    secs = rng.choice(86400, n)
    ts = (start + day.astype("timedelta64[D]")).astype("datetime64[s]") + secs.astype(
        "timedelta64[s]"
    )
    dates = (start + day.astype("timedelta64[D]")).astype("datetime64[D]")
    table = pa.table(
        {"id": np.arange(n), "at": pa.array(ts, pa.timestamp("s")), "d": pa.array(dates)}
    )
    fitted, orig, regen = roundtrip(table, "t")
    # a date32 column profiles as `datetime`, midnight timestamps as `date`: the plan says so
    assert orig["columns"]["d"]["dtype"] == "datetime" and regen["columns"]["d"]["dtype"] == "date"
    assert {i.evidence: i.status for i in fitted.plan.items}["t.d.dtype"] == "approximate"
    for col in ("at", "d"):
        o, r = orig["columns"][col], regen["columns"][col]
        assert tvd(o["dow_histogram"], r["dow_histogram"]) <= 0.02
        assert (
            tvd(o["temporal_histogram"]["month_weights"], r["temporal_histogram"]["month_weights"])
            <= 0.02
        )
    assert (
        tvd(orig["columns"]["at"]["hour_histogram"], regen["columns"]["at"]["hour_histogram"])
        <= 0.02
    )


def _season(days: int) -> np.ndarray:
    d = np.arange(days)
    w = 1.0 + 0.6 * np.sin(2 * np.pi * d / 365) + 0.3 * (d % 7 >= 5)  # yearly wave, weekend lift
    return w / w.sum()


def test_two_tables_keep_their_keys_and_relative_sizes() -> None:
    rng = np.random.default_rng(6)
    customer = pa.table({"customer_id": np.arange(1, 501), "segment": rng.choice(["a", "b"], 500)})
    orders = pa.table(
        {
            "order_id": np.arange(1, 4001),
            "customer_id": rng.integers(1, 501, 4000),
            "amount": np.round(rng.gamma(2.0, 30.0, 4000), 2),
        }
    )
    profile = shape.profile({"customer": customer, "orders": orders})
    out = shape.generate(profile, seed=3).tables
    assert out["customer"].num_rows == 500 and out["orders"].num_rows == 4000
    keys = set(out["customer"]["customer_id"].to_pylist())
    assert set(out["orders"]["customer_id"].to_pylist()) <= keys  # integrity holds
    assert out["orders"]["customer_id"].type == pa.int64()


def test_rows_override_changes_the_plan_for_row_dependent_fields() -> None:
    table = pa.table({"id": np.arange(1000), "g": ["a", "b"] * 500})
    profile = shape.profile(table, name="t")
    same = {i.evidence: i.status for i in fit_schema(profile).plan.items}
    other = {i.evidence: i.status for i in fit_schema(profile, rows=5000).plan.items}
    assert same["t.id.cardinality"] == "preserved" and other["t.id.cardinality"] == "approximate"
    assert shape.generate(profile, n=5000, seed=1).tables["t"].num_rows == 5000
    with pytest.raises(ValueError):
        fit_schema(shape.profile({"a": table, "b": table}), rows=10)
