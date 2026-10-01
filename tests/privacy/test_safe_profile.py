"""Safe profile (P7-01): no value-bearing field, k-anonymous categories, default-deny labels."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import shape
from shape.privacy import safe_profile as sp
from shape.privacy.safe_profile import (
    FORBIDDEN_RAW_FIELDS,
    ColumnConfig,
    SafeConfig,
    SafeProfile,
    to_safe_profile,
)
from shape.privacy.safe_validator import SafeProfileValidator

ROWS = 400


@pytest.fixture(scope="module")
def csv_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    lines = ["id,status,tier,store,amount,email,note,signup"]
    for i in range(ROWS):
        status = "paid" if i % 2 else "new"
        if i < 3:
            status = "vip"  # a rare category: 3 rows
        tier = "gold" if i % 10 == 0 else "basic"
        store = 100 + (i % 4)
        lines.append(
            f"{i},{status},{tier},{store},{i * 1.37:.2f},user{i}@example.com,"
            f"free text number {i} here,2020-0{1 + i % 9}-1{i % 9}"
        )
    path = tmp_path_factory.mktemp("safe") / "orders.csv"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def profile(csv_path: Path) -> shape.profile.Profile:  # type: ignore[name-defined]
    return shape.profile(str(csv_path))


def _cols(safe: SafeProfile) -> dict[str, sp.SafeColumnProfile]:
    (table,) = safe.tables.values()
    return table.columns


def _all_strings(node: object):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for k, v in node.items():
            yield str(k)
            yield from _all_strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _all_strings(v)


def test_no_declared_field_can_carry_raw_values():
    from dataclasses import fields

    for klass in (sp.SafeColumnProfile, sp.SafeTableProfile, SafeProfile):
        assert not {f.name for f in fields(klass)} & FORBIDDEN_RAW_FIELDS


def test_safe_profile_has_no_raw_extremes_values_or_pii(profile):
    safe = to_safe_profile(profile)
    doc = json.dumps(safe.to_dict())
    assert "user5@example.com" not in doc and "free text number" not in doc
    for banned in ("min_value", "max_value", "enum_values", "value_counts_ext"):
        assert banned not in doc
    assert SafeProfileValidator().validate_data(safe.to_dict()).is_clean


def test_rare_category_is_folded_into_other(profile):
    cols = _cols(to_safe_profile(profile))
    weights = cols["status"].categorical_weights
    assert weights is not None
    assert "vip" not in weights and sp.OTHER_BUCKET in weights
    # vip has 3 rows, so __OTHER__ alone would be below k=5: the smallest survivor joins it.
    assert cols["status"].suppressed_category_count == 2
    assert weights[sp.OTHER_BUCKET] * 397 >= 5
    assert sum(weights.values()) == pytest.approx(1.0, abs=1e-5)


def test_k_override_and_sensitive_flag(profile):
    # tier=gold is 40 rows: kept at k=5, folded at k=41; sensitive raises k to 11.
    assert "gold" in _cols(to_safe_profile(profile))["tier"].categorical_weights
    high = SafeConfig(columns={"tier": ColumnConfig(k=41)})
    assert "gold" not in _cols(to_safe_profile(profile, high))["tier"].categorical_weights
    cfg = SafeConfig(sensitive=True, columns={"status": ColumnConfig(k=2)})
    safe = to_safe_profile(profile, cfg)
    assert safe.redaction_manifest["k_default"] == 11
    manifest = safe.redaction_manifest["tables"]["orders"]
    assert manifest["status"]["k"] == 2 and manifest["status"]["sensitive"] is False
    assert manifest["tier"]["k"] == 11 and manifest["tier"]["sensitive"] is True


def test_pii_columns_keep_pattern_and_length_only(profile):
    cols = _cols(to_safe_profile(profile))
    for name in ("email", "id", "note"):
        c = cols[name]
        assert c.categorical_weights is None and c.categorical_histogram is None
    assert cols["email"].pattern == "email" and cols["email"].length_dist
    assert cols["note"].length_dist  # the cardinality backstop, whatever the pattern
    manifest = to_safe_profile(profile).redaction_manifest["tables"]["orders"]
    assert manifest["email"]["pattern_only"] and manifest["note"]["pattern_only"]


def test_low_cardinality_numbers_become_a_histogram_without_literals(profile):
    c = _cols(to_safe_profile(profile))["store"]
    assert c.categorical_weights is None and c.categorical_histogram
    assert c.categorical_histogram["kind"] == "numeric"
    assert c.quantiles is None and c.bounds is None and c.distribution_params is None
    assert set(c.categorical_histogram) == {"kind", "lo", "hi", "nbins", "bins"}
    assert c.categorical_histogram["lo"] == 100.0 and c.categorical_histogram["hi"] == 200.0


def test_continuous_column_carries_winsorized_bounds(profile):
    c = _cols(to_safe_profile(profile))["amount"]
    assert c.bounds and c.bounds["lo"] == c.quantiles["p1"] and c.bounds["hi"] == c.quantiles["p99"]
    full = profile.to_dict()["columns"]["amount"]
    assert c.bounds["hi"] < full["max_value"][1]  # never the raw maximum


def test_safe_labels_only_when_no_digit_can_hide_in_them():
    assert sp.is_safe_label_set("string", {"gold": 0.5, "basic": 0.5})
    assert not sp.is_safe_label_set("string", {"123-45-6789": 1.0})
    assert not sp.is_safe_label_set("integer", {"gold": 1.0})
    assert not sp.is_safe_label_set("string", {f"l{i}": 0.01 for i in range(80)})


def test_non_label_strings_are_hashed_not_literal():
    weights, hist, routed = sp._route_non_label(
        "string", {"A-17": 0.6, "B-22": 0.3, "__OTHER__": 0.1}
    )
    assert hist is None and not routed and weights is not None
    assert "A-17" not in weights and weights["__OTHER__"] == 0.1 and len(weights) == 3


def test_unsafe_opt_out_is_stamped_and_rejected(profile):
    safe = to_safe_profile(profile, unsafe_full_fidelity=True)
    assert safe.unsafe is True and safe.redaction_manifest["unsafe"] is True
    assert "vip" in _cols(safe)["status"].categorical_weights
    result = SafeProfileValidator().validate_data(safe.to_dict())
    assert [f.rule for f in result.findings if f.rule == "unsafe-stamp"]
    assert not to_safe_profile(profile).unsafe


def test_json_round_trip_is_byte_stable(profile, tmp_path):
    safe = to_safe_profile(profile)
    path = safe.save(tmp_path / "s.json")
    again = SafeProfile.load(path)
    assert again.to_json() == safe.to_json()
    assert safe.to_json() == to_safe_profile(profile).to_json()


def test_load_rejects_newer_schema_and_foreign_json(tmp_path):
    (tmp_path / "a.json").write_text(json.dumps({"tables": {}, "schema_version": 99}))
    (tmp_path / "b.json").write_text(json.dumps({"columns": {}}))
    for name in ("a.json", "b.json"):
        with pytest.raises(ValueError):
            SafeProfile.load(tmp_path / name)


def test_dataset_profile_maps_every_table_and_keeps_relationships(profile):
    table = profile.to_dict()
    dataset = {
        "tables": {"a": {**table, "name": "a"}, "b": {**table, "name": "b"}},
        "relationships": [{"parent": "a", "child": "b"}],
    }
    safe = to_safe_profile(dataset)
    assert set(safe.tables) == {"a", "b"} and set(safe.redaction_manifest["tables"]) == {"a", "b"}
    assert safe.relationships == [{"parent": "a", "child": "b"}]


def test_profile_path_and_dict_inputs_agree(profile, tmp_path):
    path = tmp_path / "p.shape"
    shape.save(profile, str(path))
    assert to_safe_profile(path).to_json() == to_safe_profile(profile.to_dict()).to_json()


def test_not_a_profile_is_an_error():
    with pytest.raises(ValueError):
        to_safe_profile({"nothing": 1})
