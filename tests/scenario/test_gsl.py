"""P6-14: the generation spec language (GSL)."""

from __future__ import annotations

import pytest

from shape.scenario import GSLParser, PackError, validate_spec
from shape.scenario.resolve import spec_domain, spec_pack
from tests.scenario.conftest import write


def test_basic_spec_fields(fixtures):
    spec = GSLParser().parse(fixtures / "retail_basic.gsl.yaml")
    assert (spec.version, spec.name) == (1, "retail_daily_demo")
    assert spec.schema is not None and (spec.schema.type, spec.schema.domain) == (
        "domain",
        "retail",
    )
    ref = spec.scenario
    assert ref is not None and (ref.pack, ref.scale, ref.seed) == (
        "tutorial_custom_pack.yaml",
        "fabric_demo",
        42,
    )
    assert ref.date_range is not None and (ref.date_range.start, ref.date_range.end) == (
        "2025-01-01",
        "2025-01-31",
    )
    lake = spec.outputs.lakehouse if spec.outputs else None
    assert lake is not None and lake.mode == "files_only"
    assert lake.landing_zone is not None and lake.landing_zone.root == "Files/landing/retail"
    assert spec.validation is not None
    assert spec.validation.gates == ["schema_conformance", "referential_integrity"]
    assert spec.validation.drift_policy == "quarantine_on_breaking_change"
    assert spec.extra_keys == ["outputs.lakehouse.formats"] or spec.extra_keys == []


def test_chaos_spec_keeps_unnamed_keys_as_config(fixtures):
    spec = GSLParser().parse(fixtures / "retail_chaos.gsl.yaml")
    assert spec.chaos is not None and spec.chaos.enabled and spec.chaos.intensity == "stormy"
    assert spec.chaos.config == {
        "warmup_days": 7,
        "escalation": "gradual",
        "breaking_change_day": 20,
    }
    assert (
        spec.validation is not None
        and spec.validation.drift_policy == "quarantine_on_breaking_change"
    )
    assert spec.outputs is not None and spec.outputs.lakehouse is not None
    assert spec.outputs.lakehouse.tables == ["customer", "order"]


def test_hybrid_spec_reads_the_eventstream_topic_prefix(fixtures):
    es = GSLParser().parse(fixtures / "retail_hybrid.gsl.yaml").outputs.eventstream  # type: ignore[union-attr]
    assert es is not None and es.enabled
    assert (es.endpoint_secret_ref, es.topic_prefix) == (
        "kv://my-workspace/eventstream_conn",
        "retail",
    )


def test_relative_paths_resolve_against_the_spec_file(fixtures):
    spec = GSLParser().parse(fixtures / "retail_basic.gsl.yaml")
    assert spec.resolve_path("x/y.parquet") == (fixtures / "x" / "y.parquet").resolve()
    assert spec_pack(spec).id == "my_custom_pack"
    assert spec_domain(spec).name == "retail"


@pytest.mark.parametrize("name", ["retail_basic", "retail_chaos", "retail_hybrid"])
def test_reference_specs_validate(fixtures, name):
    result = validate_spec(GSLParser().parse(fixtures / f"{name}.gsl.yaml"))
    assert result.is_valid, result.errors


def test_the_hybrid_spec_warns_that_events_are_not_sent(fixtures):
    result = validate_spec(GSLParser().parse(fixtures / "retail_hybrid.gsl.yaml"))
    assert any("outputs.eventstream is enabled" in w for w in result.warnings)


def test_parse_dict_and_defaults():
    spec = GSLParser().parse_dict({"name": "x"})
    assert (
        spec.version == 1 and spec.schema is None and spec.scenario is None and spec.chaos is None
    )


def test_malformed_specs(tmp_path):
    with pytest.raises(PackError, match="is empty"):
        GSLParser().parse(write(tmp_path / "a.yaml", ""))
    with pytest.raises(PackError, match="scenario.seed must be an integer"):
        GSLParser().parse(write(tmp_path / "b.yaml", "scenario: {pack: p.yaml, seed: abc}\n"))
    with pytest.raises(FileNotFoundError):
        GSLParser().parse(tmp_path / "missing.yaml")


def test_validate_spec_reports_each_problem(fixtures, tmp_path):
    text = """\
version: 2
schema: {type: spindle_json, path: x.json}
scenario: {pack: nope.yaml, scale: gigantic}
chaos: {enabled: true, intensity: wild}
outputs:
  lakehouse: {mode: sideways, landing_zone: {root: /abs}}
validation: {gates: [bogus]}
unknown_top: 1
"""
    result = validate_spec(GSLParser().parse(write(tmp_path / "s.gsl.yaml", text)))
    joined = "\n".join(result.errors)
    assert "Unsupported spec version 2" in joined
    assert "unknown schema type 'spindle_json'; choose one of: domain, schema_file" in joined
    assert "scenario: Scenario pack not found" in joined
    assert "chaos: Unknown intensity 'wild'" in joined
    assert "outputs.lakehouse.mode 'sideways' must be one of" in joined
    assert "landing_zone.root '/abs' must be a relative path" in joined
    assert any("Unknown validation gate 'bogus'" in w for w in result.warnings)
    assert "Unknown key 'unknown_top' is ignored" in result.warnings


def test_a_schema_file_spec_runs_on_a_generation_schema(tmp_path, retail):
    import json

    (tmp_path / "schema.json").write_text(json.dumps(retail.schema.to_dict()))
    write(
        tmp_path / "pack.yaml",
        "id: p\nkind: file_drop\ndomain: retail\n"
        "file_drop: {entities: [store]}\nfabric_targets: {a: 1}\n",
    )
    spec = GSLParser().parse(
        write(
            tmp_path / "s.gsl.yaml",
            "schema: {type: schema_file, path: schema.json}\n"
            "scenario: {pack: pack.yaml, scale: fabric_demo}\n",
        )
    )
    assert validate_spec(spec).is_valid
    assert spec_domain(spec).table_names == retail.schema.table_names
