import json

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


def test_394_redact_sensitive_removes_every_value_key_of_a_classified_column():
    """Issue #394: only topk and examples were removed; the SSN stayed in enum_values, samples."""
    from shape.privacy import redact_sensitive
    from shape.privacy.policy import VALUE_KEYS

    col = {"count": 100, "topk": [["x", 50]], "min": 1, "max": 9, "samples": ["123-45-6789"]}
    col |= {"enum_values": {"123-45-6789": 1.0}, "quantiles": {"p50": 5}, "histogram": [1, 2]}
    col |= {"placeholders": [{"value": "123-45-6789", "count": 7}]}
    out = redact_sensitive({"rows": 100, "columns": {"ssn": col, "id": dict(col)}}, {"ssn": "PII"})
    ssn = out["columns"]["ssn"]
    assert not (set(ssn) & VALUE_KEYS) and "123-45-6789" not in json.dumps(ssn)
    assert ssn["value_evidence_redacted"] is True and ssn["count"] == 100
    assert out["columns"]["id"]["min"] == 1  # a column below the floor is untouched


def test_650_release_for_withholds_the_joint_block_and_placeholders_of_classified_columns():
    """Issue #650: the joint block (conditionals with real labels) and a column's placeholders
    were released unchanged for columns classified above the target."""
    import random

    import pyarrow as pa

    import shape

    rng = random.Random(2)
    city = [rng.choice(["SECRETCITYA", "SECRETCITYB", "SECRETCITYC"]) for _ in range(400)]
    state = [c.replace("CITY", "STATE") for c in city]
    doc = shape.profile(pa.table({"city": city, "state": state}), name="t").to_dict()
    doc["columns"]["city"]["placeholders"] = [{"value": "SECRETCITYA", "count": 9}]
    assert "SECRET" in json.dumps(doc.get("joint"))  # the fixture has a joint block to leak
    r = release_for(doc, {"city": "CONFIDENTIAL", "state": "CONFIDENTIAL"}, "PUBLIC")
    assert r.allowed and "SECRET" not in json.dumps(r.shape)
    assert "joint" in r.removed and "columns.city.placeholders" in r.removed
    public = release_for(doc, {}, "PUBLIC")
    assert public.shape.get("joint") == doc["joint"]  # nothing classified: joint is kept
