from shape.privacy import release_for
from shape.privacy.policy import VALUE_KEYS


def test_release_policy_removes_values_and_suppresses_small_cohort():
    s = {
        "rows": 100,
        "columns": {
            "email": {
                "kind": "text",
                "count": 100,
                "topk": [["a@x.com", 9]],
                "examples": ["a@x.com"],
            },
            "rare": {"kind": "text", "count": 2, "topk": [["secret", 2]]},
        },
    }
    r = release_for(s, {"email": "PII", "rare": "TOP_SECRET"}, "PUBLIC", 5)
    raw = str(r.shape)
    assert "a@x.com" not in raw and "secret" not in raw and r.shape["columns"]["rare"]["suppressed"]


def _pii_shape(rows=100):
    return {
        "rows": rows,
        "columns": {
            "email": {
                "kind": "text",
                "count": rows,
                "topk": [["a@x.com", 9]],
                "q25": 1,
                "q50": 2,
                "q75": 3,
                "enum_values": ["a@x.com"],
                "value_counts_ext": {"a@x.com": 9},
                "pattern_examples": ["a@x.com"],
                "distribution_params": {"mu": 3.0},
            }
        },
    }


def test_pii_column_released_to_public_has_no_value_keys():  # SEC2
    r = release_for(_pii_shape(), {"email": "PII"}, "PUBLIC")
    assert r.allowed is True
    col = r.shape["columns"]["email"]
    banned = VALUE_KEYS | {
        "q25",
        "q50",
        "q75",
        "enum_values",
        "value_counts_ext",
        "pattern_examples",
        "distribution_params",
    }
    assert not banned & set(col)
    assert "a@x.com" not in str(r.shape)


def test_source_above_target_is_denied():  # SEC2
    r = release_for(_pii_shape(), {"email": "PII"}, "PUBLIC", source_classification="TOP_SECRET")
    assert r.allowed is False and r.reason == "source_exceeds_target"
    assert "a@x.com" not in str(r.shape)


def test_source_at_or_below_target_is_allowed():
    r = release_for(_pii_shape(), {}, "INTERNAL", source_classification="INTERNAL")
    assert r.allowed is True


def test_cohort_below_minimum_is_denied():  # SEC2
    r = release_for(_pii_shape(rows=3), {}, "PUBLIC", minimum_cohort=5)
    assert r.allowed is False and r.reason == "cohort_below_minimum"
    assert "a@x.com" not in str(r.shape)
