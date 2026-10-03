"""W1-11 deliverables 2 and 3: what the default capture keeps and what it suppresses."""

from __future__ import annotations

import copy
import json
from typing import Any

import pyarrow as pa
import pytest

import shape
from shape.privacy.cells import OTHER_BUCKET
from shape.privacy.redact import CaptureConfig, redact_profile

TABLE = "table"


def cols(p: Any, table: str = TABLE) -> dict[str, dict[str, Any]]:
    return p.tables[table]["columns"]


def safe(profile: Any, **kw: Any) -> Any:
    return redact_profile(profile, CaptureConfig(**kw))


def dump(p: Any) -> str:
    return json.dumps(p.to_dict(), sort_keys=True)


# --- the in-memory profile is unchanged -----------------------------------------------------------


def test_redaction_returns_a_new_profile_and_leaves_the_original_alone(profile: Any) -> None:
    before = copy.deepcopy(profile.to_dict())
    out = safe(profile)
    assert out is not profile
    assert profile.to_dict() == before
    assert cols(profile)["amount"]["max_value"] == ["float", 987654321.13]
    assert cols(out)["amount"]["max_value"] is None


def test_full_mode_keeps_every_value_and_says_so(profile: Any) -> None:
    out = safe(profile, mode="full")
    assert out.to_dict() == profile.to_dict()
    assert out.capture == {"mode": "full", "k": None}
    assert out.redaction_manifest == {}


# --- deliverable 2: sensitive columns keep statistics and formats only ----------------------------


@pytest.mark.parametrize("name", ["email", "ssn", "card", "note", "id", "amount"])
def test_pattern_only_columns_keep_statistics_and_formats_only(profile: Any, name: str) -> None:
    c = cols(safe(profile))[name]
    for gone in ("min_value", "max_value", "enum_values", "value_counts_ext"):
        assert c[gone] is None, gone
    assert c["value_counts_ext_order"] is None
    assert c["is_enum"] is False
    assert "placeholders" not in c
    assert {"min_value", "max_value", "value_counts_ext"} <= set(c["redacted"])
    # what stays
    full = cols(profile)[name]
    for kept in ("name", "dtype", "null_count", "null_rate", "cardinality", "pattern"):
        assert c[kept] == full[kept], kept
    assert c["string_length"] == full["string_length"]
    assert c["pattern_rates"] == full["pattern_rates"]
    assert c["pattern_contains_rates"] == full["pattern_contains_rates"]


def test_a_numeric_sensitive_column_gets_bounds_from_the_quantile_fingerprint(
    profile: Any,
) -> None:
    c = cols(safe(profile))["amount"]
    q = cols(profile)["amount"]["quantiles"]
    assert c["bounds"] == {"lo": q["p1"], "hi": q["p99"]}
    assert c["quantiles"] == q  # the fingerprint itself is kept
    assert "987654321" not in dump(safe(profile))


def test_a_sensitive_column_does_not_keep_a_distribution_that_names_its_extremes(
    profile: Any,
) -> None:
    # a uniform fit's loc and scale are the minimum and the range: they are the raw extremes
    full = cols(profile)["id"]
    assert full["distribution"] == "uniform" and full["distribution_params"]["loc"] == 0.0
    c = cols(safe(profile))["id"]
    assert c["distribution_params"] is None and c["distribution"] is None
    assert "distribution_params" in c["redacted"]


def test_a_non_numeric_sensitive_column_has_no_bounds(profile: Any) -> None:
    assert "bounds" not in cols(safe(profile))["email"]


@pytest.mark.parametrize("label", ["CONFIDENTIAL", "SECRET", "TOP_SECRET", "PII", "sensitive"])
def test_a_declared_classification_at_confidential_or_higher_is_sensitive(
    profile: Any, label: str
) -> None:
    out = safe(profile, classifications={"age": label})
    c = cols(out)["age"]
    assert c["enum_values"] is None and c["min_value"] is None and c["max_value"] is None
    assert c["is_enum"] is False and "bounds" in c
    m = out.redaction_manifest["tables"][TABLE]["age"]
    assert m["sensitive"] is True and m["reason"] == "classification"


@pytest.mark.parametrize("label", ["PUBLIC", "INTERNAL", "public"])
def test_a_lower_classification_is_not_sensitive(profile: Any, label: str) -> None:
    out = safe(profile, classifications={"age": label})
    c = cols(out)["age"]
    assert c["enum_values"] and c["min_value"] == ["int", 20]
    assert out.redaction_manifest["tables"][TABLE]["age"]["sensitive"] is False


def test_an_unknown_classification_label_is_an_error(profile: Any) -> None:
    with pytest.raises(ValueError, match="unknown classification"):
        safe(profile, classifications={"age": "TOPSECRET"})


def test_a_classification_for_a_column_that_is_not_there_is_an_error(profile: Any) -> None:
    # a typo must not leave the column unprotected without a word
    with pytest.raises(ValueError, match="agee"):
        safe(profile, classifications={"agee": "CONFIDENTIAL"})
    with pytest.raises(ValueError, match="nope"):
        safe(profile, column_k={"nope": 9})


def test_a_table_qualified_name_addresses_one_column(profile: Any) -> None:
    out = safe(profile, classifications={f"{TABLE}.age": "CONFIDENTIAL"})
    assert cols(out)["age"]["enum_values"] is None


def test_the_manifest_names_the_reason_each_column_is_sensitive(profile: Any) -> None:
    m = safe(profile).redaction_manifest["tables"][TABLE]
    assert m["email"]["reason"] == "pii_pattern" and m["email"]["pattern_only"] is True
    assert m["note"]["reason"] == "high_cardinality"
    assert m["card"]["reason"] == "pii_pattern"
    assert m["grade"]["sensitive"] is False and m["grade"]["reason"] is None


# --- deliverable 3: categories need k rows --------------------------------------------------------


def test_a_category_below_k_is_folded_and_one_at_k_stays(profile: Any) -> None:
    enum = cols(safe(profile))["grade"]["enum_values"]
    assert "edge5" in enum  # exactly k = 5 rows: kept
    assert "edge4" not in enum and "edge1" not in enum  # k - 1 and 1 rows: folded
    assert OTHER_BUCKET in enum
    assert enum[OTHER_BUCKET] == pytest.approx(5 / 400, abs=1e-6)
    assert sum(enum.values()) == pytest.approx(1.0, abs=1e-5)


def test_raising_k_folds_the_category_that_had_exactly_k(profile: Any) -> None:
    enum = cols(safe(profile, k=6))["grade"]["enum_values"]
    assert "edge5" not in enum
    assert enum[OTHER_BUCKET] == pytest.approx(10 / 400, abs=1e-6)


def test_column_k_overrides_k_for_one_column(profile: Any) -> None:
    out = safe(profile, k=5, column_k={"grade": 6})
    assert "edge5" not in cols(out)["grade"]["enum_values"]
    assert out.redaction_manifest["tables"][TABLE]["grade"]["k"] == 6
    assert out.redaction_manifest["tables"][TABLE]["age"]["k"] == 5


def test_a_below_k_other_bucket_absorbs_the_smallest_category() -> None:
    t = pa.table({"s": ["a"] * 100 + ["b"] * 90 + ["rare"] * 3})
    enum = cols(safe(shape.profile(t)), "table")["s"]["enum_values"]
    assert "rare" not in enum and OTHER_BUCKET in enum
    assert enum[OTHER_BUCKET] * 193 >= 5  # the smallest survivor joined the three rare rows
    assert len(enum) == 2


def test_a_column_with_fewer_than_k_rows_releases_nothing() -> None:
    t = pa.table({"s": ["a", "a", "b", "b"], "n": [1, 1, 2, 2]})
    out = safe(shape.profile(t))
    for name in ("s", "n"):
        c = cols(out)[name]
        assert c["enum_values"] is None and c["value_counts_ext"] is None, name
        assert c["is_enum"] is False and "enum_values" in c["redacted"], name


def test_exactly_k_rows_release_the_one_category_that_has_them() -> None:
    t = pa.table({"s": ["a"] * 5 + ["b"] * 5})
    enum = cols(safe(shape.profile(t)))["s"]["enum_values"]
    assert set(enum) == {"a", "b"}


def test_an_enum_whose_every_category_is_rare_releases_no_categories() -> None:
    t = pa.table({"s": ["a", "b"] * 2 + ["c"] * 2 + ["d"] * 2 + ["e"] * 2 + ["f"] * 2})
    c = cols(safe(shape.profile(t)))["s"]
    # one __OTHER__ bucket holding everything says nothing: it is not released
    assert c["enum_values"] is None and c["is_enum"] is False
    assert c["value_counts_ext"] is None


def test_the_extremes_of_a_folded_enum_do_not_name_a_folded_value(profile: Any) -> None:
    # "Zzyzx" (1 row) is the largest city: it is folded, so it cannot be the maximum either
    c = cols(safe(profile))["city"]
    assert "Zzyzx" not in c["enum_values"]
    assert c["max_value"] is None and "max_value" in c["redacted"]
    assert c["min_value"] == ["str", "Oslo"]  # a released category: it stays


def test_a_numeric_enum_with_a_folded_extreme_does_not_keep_it() -> None:
    t = pa.table({"n": [30] * 200 + [40] * 199 + [214]})
    c = cols(safe(shape.profile(t)))["n"]
    assert c["is_enum"] and c["max_value"] is None
    assert c["min_value"] == ["int", 30]


def test_the_extremes_of_a_numeric_column_that_is_not_an_enum_stay() -> None:
    rows = [i % 40 for i in range(400)]
    t = pa.table({"n": rows, "m": [float(i) / 7 for i in range(400)]})
    c = cols(safe(shape.profile(t)))["n"]
    assert c["min_value"] == ["int", 0] and c["max_value"] == ["int", 39]


def test_a_text_column_that_is_not_an_enum_keeps_no_min_or_max() -> None:
    vals = [f"word{i % 150}" for i in range(450)]  # 150 distinct, 3 rows each: not sensitive
    c = cols(safe(shape.profile(pa.table({"w": vals}))))["w"]
    assert c["min_value"] is None and c["max_value"] is None


def test_top_values_follow_the_same_cell_rule() -> None:
    vals = ["hot1"] * 100 + ["hot2"] * 100 + [f"u{i}" for i in range(800)]
    c = cols(safe(shape.profile(pa.table({"s": vals}))))["s"]
    assert c["is_enum"] is False
    assert set(c["value_counts_ext"]) == {"hot1", "hot2"}  # the single-row values are gone
    assert OTHER_BUCKET not in c["value_counts_ext"]


def test_value_counts_follow_the_released_enum() -> None:
    t = pa.table({"s": ["a"] * 100 + ["b"] * 90 + ["rare"] * 3})
    c = cols(safe(shape.profile(t)))["s"]
    assert set(c["value_counts_ext"]) <= set(c["enum_values"]) - {OTHER_BUCKET}


def test_histogram_bins_below_k_are_zeroed() -> None:
    import datetime as dt

    base = dt.datetime(2024, 1, 1)
    stamps = [base.replace(hour=9 + i % 8, day=1 + i % 28) for i in range(400)]
    stamps += [base.replace(hour=3, day=2)] * 3  # 3 rows in the 03:00 bin
    p = shape.profile(pa.table({"t": pa.array(stamps, pa.timestamp("us"))}))
    full = cols(p)["t"]["hour_histogram"]
    assert full[3] > 0
    out = cols(safe(p))["t"]["hour_histogram"]
    assert out[3] == 0 and sum(out) == pytest.approx(sum(full), abs=1e-5)
    assert cols(safe(p, k=1))["t"]["hour_histogram"][3] == full[3]


def test_placeholders_below_k_are_dropped_and_those_at_k_stay() -> None:
    vals = [-999] * 5 + [-1] * 4 + [i % 25 for i in range(391)]
    c = cols(safe(shape.profile(pa.table({"d": vals}))))["d"]
    kept = [x["value"] for x in c.get("placeholders", [])]
    assert all(x["count"] >= 5 for x in c.get("placeholders", []))
    assert "-1" not in kept and "-1.0" not in kept


# --- the artifact is stable and idempotent -------------------------------------------------------


def test_redacting_twice_changes_nothing(profile: Any) -> None:
    once = safe(profile)
    twice = redact_profile(once, CaptureConfig())
    assert twice.to_dict() == once.to_dict()
    assert twice.redaction_manifest["tables"][TABLE]["grade"]["k"] == 5


def test_a_safe_profile_cannot_be_made_full_again(profile: Any) -> None:
    with pytest.raises(ValueError, match="captured safe"):
        redact_profile(safe(profile), CaptureConfig(mode="full"))


def test_the_mode_and_k_are_checked(profile: Any) -> None:
    with pytest.raises(ValueError, match="capture"):
        CaptureConfig(mode="partial")
    with pytest.raises(ValueError, match="k"):
        CaptureConfig(k=0)
    with pytest.raises(ValueError, match="k"):
        CaptureConfig(column_k={"age": 0})
    with pytest.raises(ValueError, match="k"):
        CaptureConfig(k=True)  # type: ignore[arg-type]


def test_the_capture_record_and_manifest(profile: Any) -> None:
    out = safe(profile, k=7)
    assert out.capture == {"mode": "safe", "k": 7}
    m = out.redaction_manifest
    assert m["k_default"] == 7 and set(m["tables"]) == {TABLE}
    g = m["tables"][TABLE]["grade"]
    assert g["categories_dropped"] == 3 and g["k"] == 7
    e = m["tables"][TABLE]["email"]
    assert e["pattern_only"] and set(e["suppressed"]) >= {"min_value", "max_value"}


# --- the other surfaces: joint analysis, workbook findings, reference pairs -----------------------


def test_joint_conditionals_on_a_sensitive_column_are_dropped() -> None:
    import random

    random.seed(3)
    city = [random.choice(["Paris", "Rome", "Oslo"]) for _ in range(400)]
    zips = {"Paris": "Z75001", "Rome": "Z00100", "Oslo": "Z0150"}
    t = pa.table({"id": list(range(400)), "city": city, "zip": [zips[c] for c in city]})
    p = shape.profile(t)
    assert p.tables[TABLE]["joint"]["conditionals"]
    out = safe(p, classifications={"zip": "CONFIDENTIAL"})
    conds = out.tables[TABLE]["joint"]["conditionals"]
    assert all("zip" not in (c["given"], c["target"]) for c in conds)
    assert "Z75001" not in dump(out)
    assert "columns" not in out.tables[TABLE]["joint"]  # a list of names, not a statistic


def test_joint_conditionals_follow_the_cell_rule() -> None:
    import random

    random.seed(4)
    city = [random.choice(["Paris", "Rome"]) for _ in range(400)]
    kind = ["x" if c == "Paris" else "y" for c in city]
    city[0], kind[0] = "Atlantis", "z"  # one row each: below k
    p = shape.profile(pa.table({"id": list(range(400)), "city": city, "kind": kind}))
    out = safe(p)
    text = json.dumps(out.tables[TABLE]["joint"])
    assert "Atlantis" not in text and '"z"' not in text


def test_joint_dependency_violations_do_not_carry_small_counts() -> None:
    import random

    random.seed(5)
    city = [random.choice(["Paris", "Rome", "Oslo"]) for _ in range(400)]
    zips = {"Paris": "Z75001", "Rome": "Z00100", "Oslo": "Z0150"}
    zp = [zips[c] for c in city]
    zp[0] = "Z99999"
    zp[1] = "Z99999"
    p = shape.profile(pa.table({"id": list(range(400)), "city": city, "zip": zp}))
    assert "Z99999" in json.dumps(p.tables[TABLE]["joint"])
    assert "Z99999" not in dump(safe(p))


def _synthetic(table: dict[str, Any]) -> Any:
    from shape.profile.reference.profile import Profile

    return Profile(table, name="synthetic")


def test_workbook_findings_lose_examples_cells_and_rare_sentinels(profile: Any) -> None:
    data = profile.to_dict()
    data["findings"] = [
        {
            "kind": "sentinel_values",
            "table": "t",
            "column": "age",
            "count": 3,
            "cells": ["A2", "A3", "A4"],
            "examples": ["N/A", "N/A", "N/A"],
            "value": "N/A",
        },
        {
            "kind": "sentinel_values",
            "table": "t",
            "column": "age",
            "count": 12,
            "cells": ["B2", "B3", "B4"],
            "examples": ["-1"],
            "value": "-1",
        },
        {"kind": "hidden_sheet", "table": "t", "name": "Scratch"},
    ]
    out = safe(_synthetic(data)).to_dict()["findings"]
    assert [f["kind"] for f in out] == ["sentinel_values", "hidden_sheet"]
    assert out[0]["value"] == "-1"
    for f in out:
        assert "examples" not in f and "cells" not in f


def test_reference_pair_examples_need_k_rows_and_non_sensitive_columns(profile: Any) -> None:
    data = profile.to_dict()
    data["joint"] = {
        "version": 1,
        "reference_pairs": [
            {
                "name": "city+grade",
                "columns": ["city", "grade"],
                "reference": "ref",
                "rows": 400,
                "match_rate": 0.9,
                "mismatched": 40,
                "examples": [
                    {"values": ["Paris", "A"], "rows": 30},
                    {"values": ["Atlantis", "B"], "rows": 2},
                ],
                "sampled": False,
            },
            {
                "name": "ssn",
                "columns": ["ssn"],
                "reference": "ref",
                "rows": 400,
                "match_rate": 0.9,
                "mismatched": 40,
                "examples": [{"values": ["078-05-1120"], "rows": 30}],
                "sampled": False,
            },
        ],
    }
    pairs = safe(_synthetic(data)).tables[TABLE]["joint"]["reference_pairs"]
    assert pairs[0]["examples"] == [{"values": ["Paris", "A"], "rows": 30}]
    assert pairs[1]["examples"] == []
    assert pairs[1]["mismatched"] == 40  # the count is a statistic


# --- a dataset: one table at a time --------------------------------------------------------------


def test_a_dataset_is_redacted_table_by_table() -> None:
    a = pa.table({"k": list(range(200)), "tag": ["x"] * 198 + ["rare"] * 2})
    b = pa.table({"k": list(range(200)), "tag": ["y"] * 150 + ["z"] * 50})
    p = shape.profile({"a": a, "b": b})
    out = safe(p, classifications={"b.tag": "CONFIDENTIAL"})
    assert cols(out, "b")["tag"]["enum_values"] is None
    assert cols(out, "a")["tag"]["enum_values"] is None  # 198 + 2 rows: all folded together
    assert out.to_dict()["relationships"] == p.to_dict()["relationships"]
    assert set(out.redaction_manifest["tables"]) == {"a", "b"}
    # a bare name that two tables share addresses both; a qualified one only its own
    both = safe(p, classifications={"tag": "CONFIDENTIAL"})
    assert cols(both, "a")["tag"]["enum_values"] is None
    assert cols(both, "b")["tag"]["enum_values"] is None
