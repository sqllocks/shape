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
