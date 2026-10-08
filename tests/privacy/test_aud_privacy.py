"""Regression tests for the AUD-privacy audit (privacy package)."""

from __future__ import annotations

import pyarrow as pa
import pytest

import shape
from shape.privacy import redact_sensitive
from shape.privacy.policy import VALUE_KEYS
from shape.privacy.safe_profile import SafeConfig, to_safe_profile
from shape.privacy.safe_validator import SafeProfileValidator

# --- #394: redact_sensitive removes every value-bearing key ----------------------------------


def _evidence_column() -> dict:
    col = {k: [1, 2] for k in VALUE_KEYS}
    col.update(count=100, kind="string", topk=[["123-45-6789", 50]])
    return col


def test_redact_sensitive_removes_every_value_key_of_a_classified_column():
    out = redact_sensitive({"rows": 100, "columns": {"ssn": _evidence_column()}}, {"ssn": "PII"})
    col = out["columns"]["ssn"]
    assert not set(col) & VALUE_KEYS
    assert col["value_evidence_redacted"] is True and col["count"] == 100


def test_redact_sensitive_keeps_the_evidence_of_a_public_column():
    out = redact_sensitive({"rows": 100, "columns": {"c": _evidence_column()}}, {})
    assert set(VALUE_KEYS) <= set(out["columns"]["c"])


# --- #395: a column below the minimum cohort releases no value statistic -----------------------

_VALUE_STATS = ("mean", "std", "quantiles", "bounds", "distribution_params")


def _col(safe, name):
    t = safe.to_dict()["tables"]
    return t[next(iter(t))]["columns"][name]


def test_one_row_column_does_not_publish_its_value():
    safe = to_safe_profile(shape.profile(pa.table({"salary": [123456.0]})))
    col = _col(safe, "salary")
    assert all(col[k] is None for k in _VALUE_STATS)
    assert "123456" not in safe.to_json()


def test_column_with_fewer_non_null_rows_than_k_withholds_value_statistics():
    prof = {
        "name": "t",
        "row_count": 100,
        "columns": {
            "x": {
                "name": "x",
                "dtype": "float",
                "null_count": 97,
                "cardinality": 3,
                "mean": 7.0,
                "std": 1.0,
                "quantiles": {"p1": 6.0, "p50": 7.0, "p99": 8.0},
                "distribution": "normal",
                "distribution_params": {"loc": 7.0, "scale": 1.0},
            }
        },
    }
    col = to_safe_profile(prof).to_dict()["tables"]["t"]["columns"]["x"]
    assert all(col[k] is None for k in _VALUE_STATS)
    # the per-column k decides: k=3 releases them
    cfg = SafeConfig(k=3)
    col3 = to_safe_profile(prof, cfg).to_dict()["tables"]["t"]["columns"]["x"]
    assert col3["mean"] == 7.0 and col3["bounds"] == {"lo": 6.0, "hi": 8.0}


def test_unsafe_full_fidelity_keeps_the_value_statistics_of_a_small_column():
    safe = to_safe_profile(
        shape.profile(pa.table({"salary": [123456.0]})), unsafe_full_fidelity=True
    )
    assert _col(safe, "salary")["mean"] == 123456.0


@pytest.mark.parametrize("n", [5, 50])
def test_columns_at_or_above_k_keep_their_mean(n):
    safe = to_safe_profile(shape.profile(pa.table({"v": [float(i) for i in range(n)]})))
    assert _col(safe, "v")["mean"] == pytest.approx((n - 1) / 2)
    assert SafeProfileValidator().validate_data(safe.to_dict()).is_clean


# --- #398: the leak scanner fails closed when tables is not an object -----------------------


@pytest.mark.parametrize("tables", [[{"row_count": 0}], "t", 5, None])
def test_tables_that_is_not_an_object_is_a_finding(tables):
    res = SafeProfileValidator().validate_data({"schema_version": 1, "tables": tables})
    assert ("row-count-missing", "$.tables") in [(f.rule, f.path) for f in res.findings]
    assert res.exit_code == 1


def test_a_document_without_tables_is_not_flagged_for_row_counts():
    res = SafeProfileValidator().validate_data({"schema_version": 1, "redaction_manifest": {}})
    assert res.is_clean


# --- #400: dates are not phone numbers ----------------------------------------------------------


@pytest.mark.parametrize(
    "value", ["2024-01-15", "2024/01/15", "15/01/2024", "01-15-2024", "15.01.2024", "2024.01.15"]
)
def test_a_date_is_not_a_phone_number(value):
    from shape.privacy import detect_value

    assert "phone" not in [d.kind for d in detect_value(value)]


@pytest.mark.parametrize("value", ["555-123-4567", "+1 (555) 123-4567", "020 7946 0958"])
def test_phone_numbers_are_still_phone_numbers(value):
    from shape.privacy import detect_value

    assert "phone" in [d.kind for d in detect_value(value)]


def test_a_date_column_is_not_detected_as_phone():
    from shape.privacy import detect_column

    assert detect_column([f"2024-01-{d:02d}" for d in range(1, 29)]) == ()


# --- #412: non-finite numeric enum keys do not crash the safe profile ---------------------------


@pytest.mark.parametrize("bad", ["inf", "-inf", "nan", "1e400"])
def test_a_non_finite_numeric_key_falls_back_to_hashed_keys(bad):
    prof = {
        "name": "t",
        "row_count": 100,
        "columns": {
            "x": {
                "name": "x",
                "dtype": "float",
                "is_enum": True,
                "cardinality": 2,
                "null_count": 0,
                "enum_values": {"1.0": 0.5, bad: 0.5},
            }
        },
    }
    col = to_safe_profile(prof).to_dict()["tables"]["t"]["columns"]["x"]
    assert col["categorical_histogram"] is None
    assert len(col["categorical_weights"]) == 2 and bad not in col["categorical_weights"]


# --- #416: differential privacy over a column with a non-finite value --------------------------


def test_dp_noises_the_finite_values_and_keeps_the_non_finite_ones():
    import math
    import warnings

    from shape.privacy.dp import DifferentialPrivacy

    t = pa.table({"a": pa.array([1.0, 2.0, 3.0, float("inf"), float("-inf"), None])})
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        out, res = DifferentialPrivacy().apply(t, seed=1)
    a = out.column("a").to_pylist()
    assert all(1.0 <= v <= 3.0 for v in a[:3])
    assert a[3] == math.inf and a[4] == -math.inf and a[5] is None
    assert res.actual_sensitivity == {"a": 2.0}


def test_dp_finite_columns_are_unchanged_by_the_fix():
    from shape.privacy.dp import DifferentialPrivacy

    t = pa.table({"a": pa.array([1.0, 2.0, 3.0, None]), "b": pa.array([1, 5, 9, 2])})
    out, _ = DifferentialPrivacy().apply(t, seed=7)
    import numpy as np

    gen = np.random.default_rng(7)
    na = gen.laplace(0, 2.0, size=4)
    nb = gen.laplace(0, 8.0, size=4)
    assert out.column("a").to_pylist()[:3] == list(np.clip(np.array([1.0, 2, 3]) + na[:3], 1, 3))
    assert out.column("b").to_pylist() == list(np.clip(np.array([1.0, 5, 9, 2]) + nb, 1, 9))


# --- #424: suppress_shape and redact_sensitive argument edges -----------------------------------


def test_suppress_shape_withholds_a_column_with_an_unknown_count():
    from shape.privacy import suppress_shape

    out = suppress_shape({"rows": 10, "columns": {"a": {"kind": "x", "count": None}}})
    assert out["columns"]["a"]["suppressed"] is True


def test_redact_sensitive_needs_a_classification():
    with pytest.raises(ValueError, match="redact_at"):
        redact_sensitive({"rows": 1, "columns": {}}, {}, redact_at=())


# --- #109: privacy commands print the not-signed notice as a note -------------------------------


def test_profile_safe_prints_the_notice_as_a_note(tmp_path, capsys):
    import pyarrow.parquet as pq

    from shape.cli.main import main

    pq.write_table(pa.table({"a": list(range(20))}), tmp_path / "t.parquet")
    assert main(["profile", str(tmp_path / "t.parquet"), "-o", str(tmp_path / "p.shape")]) == 0
    capsys.readouterr()
    rc = main(["profile", "safe", str(tmp_path / "p.shape"), "-o", str(tmp_path / "s.json")])
    err = capsys.readouterr().err
    assert rc == 0
    assert "shape: note:" in err and "not signed" in err
    assert "Warning" not in err and ".py:" not in err


# --- coverage: assess_summary (privacy.core) ----------------------------------------------------


def test_assess_summary_flags_rare_values_and_near_unique_fields():
    from shape.privacy import assess_summary

    report = assess_summary({"topk": [["a", 3], ["b", 50]], "count": 100, "distinct_estimate": 99})
    assert [f.kind for f in report.findings] == ["rare_value", "near_unique"]
    assert report.releasable is False


def test_assess_summary_releases_a_common_low_cardinality_field():
    from shape.privacy import assess_summary

    report = assess_summary({"topk": [["a", 60], ["b", 40]], "count": 100, "distinct_estimate": 2})
    assert report.findings == () and report.releasable is True
    assert assess_summary({}).releasable is True
