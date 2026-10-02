"""Tier 2: format preservation, string similarity, cardinality, anomaly rate."""

from __future__ import annotations

import json

import numpy as np
import pyarrow as pa
import pytest
from fid_helpers import make_table

from shape.fidelity.tier2 import (
    ANOMALY_COLUMN,
    check_anomaly_rates,
    check_cardinality,
    run_tier2,
)


def test_dominant_format_is_found_and_compared():
    real = make_table(1, email_rate=0.95)
    same = make_table(2, email_rate=0.95)
    worse = make_table(3, email_rate=0.5)
    ok = run_tier2(real, same).format_preservation["email"]
    assert ok.detected_format == "email" and ok.passed and ok.delta < 0.1
    bad = run_tier2(real, worse).format_preservation["email"]
    assert not bad.passed and bad.delta > 0.3
    assert bad.real_format_rate == pytest.approx(0.95, abs=0.05)


def test_no_clear_format_means_no_result():
    t = pa.table({"w": [f"word{i}" for i in range(100)]})
    assert run_tier2(t, t).format_preservation == {}


@pytest.mark.parametrize(
    ("fmt", "good", "bad"),
    [
        ("uuid", "123e4567-e89b-12d3-a456-426614174000", "123e4567"),
        ("zip_us", "12345-6789", "1234"),
        ("ipv4", "10.0.0.1", "10.0.0"),
        ("date_iso", "2024-05-01", "05/01/2024"),
        ("ssn_us", "123-45-6789", "123456789"),
        ("credit_card", "4111 1111 1111 1111", "4111"),
        ("url", "https://a.b/c", "ftp://a"),
        ("phone_us", "(555) 123-4567", "123"),
    ],
)
def test_every_format(fmt, good, bad):
    t = pa.table({"c": [good] * 30 + [bad] * 2})
    r = run_tier2(t, t).format_preservation["c"]
    assert r.detected_format == fmt


def test_format_check_samples_500_rows_like_a_data_frame_sample():
    # 1000 rows, the first 600 valid emails: the sample of 500 is not simply the first 500
    vals = [f"a{i}@b.co" if i < 600 else "x" for i in range(1000)]
    r = run_tier2(pa.table({"c": vals}), pa.table({"c": vals})).format_preservation["c"]
    import pandas as pd

    sample = pd.Series(vals).sample(500, random_state=0)
    assert r.real_format_rate == pytest.approx(sample.str.contains("@").mean())


def test_string_similarity_identical_and_different():
    a = pa.table({"s": [f"alpha beta {i}" for i in range(50)]})
    b = pa.table({"s": [f"zzz qqq {i * 7}" for i in range(50)]})
    assert run_tier2(a, a).string_similarity["s"].cosine_similarity == pytest.approx(1.0)
    r = run_tier2(a, a).string_similarity["s"]
    assert r.score == 100.0 and r.ngram_n == 3
    assert run_tier2(a, b).string_similarity["s"].cosine_similarity < 0.5


def test_short_text_columns_are_left_out():
    t = pa.table({"s": [f"v{i}" for i in range(9)]})
    assert run_tier2(t, t).string_similarity == {}


def test_cardinality_ratio_and_deviation():
    real = pa.table({"a": list(range(100)), "b": [1] * 100, "_shape_x": [1] * 100})
    synth = pa.table({"a": list(range(70)) * 1 + [0] * 30, "b": [1] * 100, "_shape_x": [1] * 100})
    res = check_cardinality(real, synth)
    assert set(res) == {"a", "b"}  # _shape_ columns are internal
    assert res["a"].real_cardinality == 100 and res["a"].synth_cardinality == 70
    assert res["a"].ratio == 0.7 and res["a"].deviation == 0.3 and not res["a"].passed
    assert res["b"].passed


def test_cardinality_skips_empty_and_counts_distinct_values_not_nulls():
    real = pa.table({"e": pa.array([None, None], type=pa.int64()), "f": [1.0, float("nan")]})
    res = check_cardinality(real, real)
    assert "e" not in res and res["f"].real_cardinality == 1


def test_anomaly_rate():
    flags = np.zeros(1000, dtype=bool)
    flags[:100] = True
    t = pa.table({"x": range(1000), ANOMALY_COLUMN: flags})
    r = check_anomaly_rates(t, {"typo": 0.06, "gap": 0.04})
    assert r is not None and r.anomaly_count == 100 and r.actual_fraction == 0.1
    assert r.expected_fraction == pytest.approx(0.1) and r.passed
    r = check_anomaly_rates(t)
    assert r is not None and r.expected_fraction == 0.0 and r.delta == 0.1 and not r.passed
    assert check_anomaly_rates(t, tolerance=0.2).passed  # type: ignore[union-attr]
    assert check_anomaly_rates(pa.table({"x": [1]})) is None


def test_anomaly_flag_may_be_integers():
    t = pa.table({ANOMALY_COLUMN: [1, 0, 0, 1]})
    r = check_anomaly_rates(t, {"a": 0.5})
    assert r is not None and r.anomaly_count == 2 and r.passed


def test_report_passing_rate_summary_and_json():
    real = make_table(1, email_rate=0.95)
    synth = make_table(2, email_rate=0.5)
    rep = run_tier2(real, synth)
    rate = rep.passing_rate()
    checks = [r.passed for r in rep.format_preservation.values()]
    checks += [r.passed for r in rep.cardinality.values()]
    assert rate == sum(checks) / len(checks) and rate < 1.0
    text = rep.summary()
    assert "Tier 2 fidelity report" in text and "[FAIL] email" in text
    d = rep.to_dict()
    json.dumps(d, allow_nan=False)
    assert d["passing_rate"] == rate
    assert run_tier2(pa.table({"a": [1]}), pa.table({"b": [1]})).passing_rate() == 1.0
