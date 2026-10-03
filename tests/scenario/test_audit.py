"""AUD-scenario: regression tests for the defects the audit lane found (one section per issue)."""

from __future__ import annotations

from pathlib import Path

import pytest

from shape.generation.schema import GenSchema
from shape.scenario import GSLParser, PackLoader, PackRunner, PackValidator, validate_spec

FILE_DROP = {
    "pack_version": 1,
    "id": "t",
    "kind": "file_drop",
    "domain": "",
    "description": "",
    "fabric_targets": {"x": 1},
    "file_drop": {"formats": ["csv"], "entities": ["customer"]},
}


def no_presets(retail, domain: str | None = None) -> GenSchema:
    """The retail schema with no scale presets (so any scale name is taken)."""
    doc = retail.schema.to_dict()
    doc["generation"]["scales"] = {}
    doc["generation"].pop("scale", None)
    if domain is not None:
        doc["model"]["domain"] = domain
    return GenSchema.from_dict(doc)


def everything_under(root: Path) -> set[Path]:
    return {p.resolve() for p in root.rglob("*")}


# ---- #281: the run id is built from plain names, the manifest stays in the output ---------------


def test_281_a_scale_name_with_a_path_does_not_leave_the_output(tmp_path, retail):
    out = tmp_path / "a" / "b"
    pack = PackLoader().parse(FILE_DROP)
    result = PackRunner().run(pack, no_presets(retail), "../../../evil", 1, out)
    assert result.is_success, result.errors
    manifest = Path(result.files_written[-1])
    assert manifest.parent.resolve() == out.resolve()
    assert "/" not in result.manifest.run_id and "\\" not in result.manifest.run_id
    outside = everything_under(tmp_path) - everything_under(out) - {out.resolve()}
    assert outside == {(tmp_path / "a").resolve()}
    assert result.manifest.scale == "../../../evil"  # the record keeps what was asked


def test_281_a_domain_name_with_a_path_does_not_leave_the_output(tmp_path, retail):
    out = tmp_path / "work" / "out"
    pack = PackLoader().parse(FILE_DROP)
    schema = no_presets(retail, domain="../../../../ESCAPED_DOMAIN")
    result = PackRunner().run(pack, schema, "small", 1, out)
    assert result.is_success, result.errors
    assert Path(result.files_written[-1]).parent.resolve() == out.resolve()
    escaped = [p for p in tmp_path.rglob("*ESCAPED_DOMAIN*") if p.parent.resolve() != out.resolve()]
    assert escaped == []


# ---- #510: the chaos section is validated ------------------------------------------------------


def chaos_pack(chaos: dict) -> dict:
    return {**FILE_DROP, "domain": "retail", "chaos": chaos}


@pytest.mark.parametrize(
    ("chaos", "key"),
    [
        ({"enabled": True, "seed": "abc"}, "chaos.seed"),
        ({"enabled": True, "warmup_days": [1]}, "chaos.warmup_days"),
        ({"enabled": True, "day": "x"}, "chaos.day"),
        ({"enabled": True, "breaking_change_day": True}, "chaos.breaking_change_day"),
        ({"enabled": "false"}, "chaos.enabled"),
        ({"enabled": True, "intensity": 3}, "chaos.intensity"),
        ({"enabled": True, "categories": ["value"]}, "chaos.categories"),
        ({"enabled": True, "categories": {"value": "x"}}, "chaos.categories.value"),
        ({"enabled": True, "config": [1]}, "chaos.config"),
        ({"enabled": True, "config": {"seed": "abc"}}, "chaos.config.seed"),
    ],
)
def test_510_a_chaos_value_of_the_wrong_type_is_an_error_naming_the_key(retail, chaos, key):
    pack = PackLoader().parse(chaos_pack(chaos))
    result = PackValidator().validate(pack, retail)
    assert not result.is_valid
    assert any(key in e for e in result.errors), result.errors


def test_510_a_run_with_a_wrong_chaos_value_fails_validation_without_a_crash(tmp_path, retail):
    pack = PackLoader().parse(chaos_pack({"enabled": "false", "day": "x"}))
    result = PackRunner().run(pack, retail, "small", 1, tmp_path / "out")
    assert not result.is_success and not result.chaos_applied
    assert any("chaos.enabled" in e for e in result.errors), result.errors


def test_510_an_unknown_chaos_key_is_warned_about(retail):
    pack = PackLoader().parse(chaos_pack({"enabled": True, "intensty": "stormy"}))
    result = PackValidator().validate(pack, retail)
    assert result.is_valid
    assert "Unknown key 'chaos.intensty' is ignored" in result.warnings


SPEC = """\
version: 1
schema: {type: domain, domain: retail}
scenario: {pack: pack.yaml, scale: small, seed: 1}
chaos: {chaos_body}
"""


def spec_with_chaos(tmp_path, body: str):
    import yaml

    (tmp_path / "pack.yaml").write_text(yaml.safe_dump(FILE_DROP | {"domain": "retail"}))
    (tmp_path / "s.gsl.yaml").write_text(SPEC.replace("{chaos_body}", body))
    return GSLParser().parse(tmp_path / "s.gsl.yaml")


def test_510_validate_spec_reports_a_wrong_chaos_value(tmp_path):
    spec = spec_with_chaos(tmp_path, "{enabled: true, seed: abc, intensty: x}")
    result = validate_spec(spec)
    assert any("chaos.seed" in e for e in result.errors), result.errors
    assert any("chaos.intensty" in w for w in result.warnings), result.warnings


def test_510_a_spec_run_validates_the_spec_chaos(tmp_path, retail):
    spec = spec_with_chaos(tmp_path, "{enabled: true, config: {seed: abc}}")
    pack = PackLoader().parse(FILE_DROP | {"domain": "retail"})
    result = PackRunner().run(pack, retail, "small", 1, tmp_path / "out", spec=spec)
    assert not result.is_success and not result.chaos_applied
    assert any("chaos.seed" in e or "chaos.config.seed" in e for e in result.errors), result.errors


# ---- #513: an unknown file format is reported --------------------------------------------------


@pytest.mark.parametrize("fmt", ["delta", "Parquet", "avro"])
def test_513_an_unknown_file_format_is_warned_about(retail, fmt):
    pack = PackLoader().parse({**FILE_DROP, "file_drop": {"formats": [fmt], "entities": []}})
    warnings = PackValidator().validate(pack, retail).warnings
    assert any(f"'{fmt}'" in w and "csv" in w for w in warnings), warnings


def test_513_an_unknown_micro_batch_format_is_warned_about(retail):
    hybrid = {"micro_batch": {"formats": ["orc"], "entities": ["customer"]}}
    pack = PackLoader().parse({**FILE_DROP, "kind": "hybrid", "hybrid": hybrid})
    warnings = PackValidator().validate(pack, retail).warnings
    assert any("hybrid.micro_batch.formats" in w and "'orc'" in w for w in warnings), warnings


def test_513_a_known_format_is_not_warned_about(retail):
    for fmt in ("parquet", "csv", "jsonl", "json"):
        pack = PackLoader().parse({**FILE_DROP, "file_drop": {"formats": [fmt], "entities": []}})
        assert not any("format" in w for w in PackValidator().validate(pack, retail).warnings)


# ---- #514: the same-second suffix keeps the whole run id ---------------------------------------


def test_514_a_second_run_in_the_same_second_keeps_scale_and_seed(tmp_path, retail, monkeypatch):
    from datetime import UTC, datetime

    import shape.scenario.manifest as manifest_module

    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):  # type: ignore[override]
            return datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

    monkeypatch.setattr(manifest_module, "datetime", Frozen)
    pack = PackLoader().parse(FILE_DROP)
    schema = no_presets(retail)
    ids = [
        PackRunner().run(pack, schema, "xlarge", 7, tmp_path / "out").manifest.run_id
        for _ in range(3)
    ]
    base = "20260102_030405_retail_xlarge_s7"
    assert ids == [base, f"{base}_x2", f"{base}_x3"]


# ---- #515: a topic listed twice is an error ----------------------------------------------------

STREAM_TOPICS = {
    "pack_version": 1,
    "id": "s",
    "kind": "stream",
    "domain": "retail",
    "description": "",
    "fabric_targets": {"x": 1},
}


def stream_pack(topics: list[dict], rate: float = 5.0):
    streaming = {"cadence": {"rate_per_sec": rate}, "topics": topics}
    return PackLoader().parse({**STREAM_TOPICS, "streaming": streaming})


def test_515_a_topic_listed_twice_is_an_error_and_the_run_writes_nothing_twice(tmp_path, retail):
    topic = {"name": "order", "event_type": "e", "payload_fields": ["order_id"]}
    pack = stream_pack([topic, dict(topic)])
    errors = PackValidator().validate(pack, retail).errors
    assert any("listed twice" in e and "order_e.jsonl" in e for e in errors), errors
    result = PackRunner().run(pack, retail, "small", 1, tmp_path / "out")
    assert not result.is_success and result.events_emitted == 0


def test_515_the_same_topic_with_another_event_type_is_fine(retail):
    topics = [
        {"name": "order", "event_type": "placed", "payload_fields": ["order_id"]},
        {"name": "order", "event_type": "paid", "payload_fields": ["order_id"]},
    ]
    assert PackValidator().validate(stream_pack(topics), retail).is_valid


# ---- #516: a stream rate must be a positive, finite number -------------------------------------


@pytest.mark.parametrize("rate", [float("nan"), float("inf"), 0.0, -1.0])
def test_516_a_stream_rate_that_is_not_positive_and_finite_is_an_error(retail, rate):
    topic = {"name": "order", "event_type": "e", "payload_fields": ["order_id"]}
    errors = PackValidator().validate(stream_pack([topic], rate), retail).errors
    assert "Streaming rate_per_sec must be positive" in errors, errors


@pytest.mark.parametrize("rate", [float("nan"), -1.0])
def test_516_a_hybrid_stream_rate_is_checked(retail, rate):
    hybrid = {"stream": {"rate_per_sec": rate, "topics": []}}
    pack = PackLoader().parse({**FILE_DROP, "kind": "hybrid", "hybrid": hybrid})
    errors = PackValidator().validate(pack, retail).errors
    assert "hybrid.stream.rate_per_sec must be positive" in errors, errors


# ---- #519: the run manifest reader checks the version and names the file -----------------------


@pytest.mark.parametrize("version", ["true", "false", "0", "-1", "1.0"])
def test_519_a_version_that_is_not_an_integer_from_one_is_refused(tmp_path, version):
    from shape.scenario import ManifestBuilder

    path = tmp_path / "m.json"
    path.write_text(f'{{"format": "shape-run-manifest", "version": {version}}}', encoding="utf-8")
    with pytest.raises(ValueError, match="m.json"):
        ManifestBuilder.from_file(path)


def test_519_a_manifest_that_is_not_json_names_the_file(tmp_path):
    from shape.scenario import ManifestBuilder

    path = tmp_path / "broken_manifest.json"
    path.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="broken_manifest.json"):
        ManifestBuilder.from_file(path)


# ---- #525: a landing root through a link out of the output creates nothing outside -------------


def test_525_a_landing_root_through_a_link_creates_nothing_outside(tmp_path, retail):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    (out / "Files").symlink_to(elsewhere, target_is_directory=True)
    targets = {"lakehouse_files_root": "Files/landing/retail"}
    pack = PackLoader().parse({**FILE_DROP, "fabric_targets": targets})
    result = PackRunner().run(pack, retail, "small", 1, out)
    assert not result.is_success and any("leaves the output" in e for e in result.errors)
    assert list(elsewhere.iterdir()) == []
