"""P6-14: the pack validator."""

from __future__ import annotations

import pytest

from shape.scenario import PackLoader, PackValidator
from tests.scenario.conftest import HYBRID, PACK, STREAM, write


def check(tmp_path, retail, text):
    pack = PackLoader().load(write(tmp_path / "p.yaml", text))
    return PackValidator().validate(pack, retail)


def test_reference_packs_are_valid_and_clean(fixtures, retail):
    for name in ("tutorial_custom_pack.yaml", "notebook_custom_pack.yaml"):
        result = PackValidator().validate(PackLoader().load(fixtures / name), retail)
        assert result.is_valid and result.warnings == []
        assert result.summary() == "Pack validation: PASS"


def test_an_unknown_entity_is_an_error_listing_the_tables(tmp_path, retail):
    result = check(
        tmp_path, retail, PACK.format(fmt="csv").replace("[customer, order]", "[customer, nope]")
    )
    assert not result.is_valid
    assert result.errors[0].startswith(
        "Entity 'nope' referenced in pack but not found in domain schema. "
        "Available tables: address,"
    )


def test_kind_version_and_sections(tmp_path, retail):
    bad = (
        PACK.format(fmt="csv")
        .replace("kind: file_drop", "kind: batch")
        .replace("pack_version: 1", "pack_version: 0")
    )
    errors = check(tmp_path, retail, bad).errors
    assert "Invalid pack_version: 0" in errors
    assert any(
        e.startswith("Invalid kind 'batch'. Must be one of: file_drop, hybrid, stream")
        for e in errors
    )
    assert (
        "Pack kind is 'stream' but no streaming section defined"
        in check(tmp_path, retail, "kind: stream\nid: x\ndomain: retail\n").errors
    )
    assert (
        "Hybrid pack must define at least micro_batch or stream"
        in check(
            tmp_path, retail, "kind: hybrid\nid: x\ndomain: retail\nhybrid: {stream_to: x}\n"
        ).errors
    )


def test_warnings_match_the_reference_wording(tmp_path, retail):
    text = (
        "kind: file_drop\nid: x\ndomain: hr\nfile_drop: {cadence: yearly}\n"
        "validation: {required_gates: [bogus]}\n"
    )
    warnings = check(tmp_path, retail, text).warnings
    assert "Pack domain 'hr' does not match domain 'retail'" in warnings
    assert any(
        w.startswith("Unusual cadence 'yearly'. Common values: daily, every_15m") for w in warnings
    )
    assert "file_drop section has no entities listed" in warnings
    assert "No fabric_targets defined — pack cannot target Fabric resources" in warnings
    assert any(
        w.startswith("Unknown validation gate 'bogus'. Known gates: null_check,") for w in warnings
    )


def test_stream_and_hybrid_topics(tmp_path, retail):
    result = check(tmp_path, retail, STREAM)
    assert result.is_valid
    assert (
        "Topic 'nothing_like_it' matches no table of the domain and emits no events"
        in result.warnings
    )
    assert check(tmp_path, retail, HYBRID).is_valid
    zero = STREAM.replace("rate_per_sec: 5", "rate_per_sec: 0")
    assert "Streaming rate_per_sec must be positive" in check(tmp_path, retail, zero).errors


def test_a_topic_cannot_name_a_path(tmp_path, retail):
    text = STREAM.replace("name: order,", "name: ../../x,")
    assert any("not usable in a file name" in e for e in check(tmp_path, retail, text).errors)


@pytest.mark.parametrize("root", ["/etc", "../outside", "a/../../b", "C:\\x", "\\\\host\\share"])
def test_a_landing_root_cannot_leave_the_output_directory(tmp_path, retail, root):
    text = PACK.format(fmt="csv").replace("Files/landing", root.replace("\\", "\\\\"))
    text = text.replace(
        f"lakehouse_files_root: {root}",
        f'lakehouse_files_root: "{root.replace(chr(92), chr(92) * 2)}"',
    )
    assert any(
        "must be a relative path inside the output directory" in e
        for e in check(tmp_path, retail, text).errors
    )


def test_chaos_settings_are_validated_only_when_enabled(tmp_path, retail):
    base = PACK.format(fmt="csv")
    assert check(tmp_path, retail, base + "chaos: {enabled: false, intensity: wild}\n").is_valid
    errors = check(tmp_path, retail, base + "chaos: {enabled: true, intensity: wild}\n").errors
    assert errors == [
        "chaos: Unknown intensity 'wild'. Choose from: calm, moderate, stormy, hurricane"
    ]


def test_keys_and_unsimulated_features_warn(tmp_path, retail):
    text = PACK.format(fmt="csv") + "typo_key: 1\nfailure_injection: {enabled: true}\n"
    text = text.replace("  entities:", "  lateness: {enabled: true, probability: 0.1}\n  entities:")
    warnings = check(tmp_path, retail, text).warnings
    assert "Unknown key 'typo_key' is ignored" in warnings
    assert any(
        "file_drop.lateness is enabled but a pack run does not simulate it" == w for w in warnings
    )
    assert any(w.startswith("failure_injection is enabled but") for w in warnings)


def test_a_broken_domain_is_reported_not_raised(tmp_path):
    pack = PackLoader().load(write(tmp_path / "p.yaml", PACK.format(fmt="csv")))
    result = PackValidator().validate(pack, object())
    assert result.errors and result.errors[0].startswith("Failed to load domain schema")
