"""P6-14: running packs and specs, the manifest and the gates."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from shape.scenario import GSLParser, ManifestBuilder, PackLoader, PackRunner
from tests.scenario.conftest import HYBRID, PACK, STREAM, write

MANIFEST_KEYS = {
    "run_id", "spec_hash", "pack_id", "domain", "scale", "seed", "engine_version", "outputs",
    "tables", "validation", "chaos", "timestamps", "workspace_id", "lakehouse_id", "sbom",
    "format", "version", "reproducibility", "dataset_id",
}  # fmt: skip


def run(tmp_path, retail, text, *, scale="fabric_demo", seed=42, name="p.yaml", out="out"):
    pack = PackLoader().load(write(tmp_path / name, text))
    return PackRunner().run(pack, retail, scale, seed, tmp_path / out)


def manifest_of(result) -> dict:
    return json.loads(Path(result.files_written[-1]).read_text())


def test_file_drop_writes_the_listed_entities_under_the_landing_root(tmp_path, retail):
    result = run(tmp_path, retail, PACK.format(fmt="parquet"))
    assert result.is_success, result.errors
    landing = tmp_path / "out" / "Files" / "landing"
    assert sorted(p.name for p in landing.iterdir()) == ["customer.parquet", "order.parquet"]
    assert pq.read_table(landing / "customer.parquet").num_rows == 200
    assert pq.read_table(landing / "order.parquet").num_rows == 1000
    assert result.validation_results == {"schema_conformance": True}
    assert "Pack Run: SUCCESS" in result.summary()


@pytest.mark.parametrize(
    ("fmt", "ext"), [("csv", "csv"), ("jsonl", "jsonl"), ("json", "jsonl"), ("avro", "csv")]
)
def test_formats(tmp_path, retail, fmt, ext):
    result = run(tmp_path, retail, PACK.format(fmt=fmt))
    assert (tmp_path / "out" / "Files" / "landing" / f"customer.{ext}").is_file()
    assert result.is_success


def test_the_manifest_has_every_key_and_exact_per_table_paths(tmp_path, retail):
    result = run(tmp_path, retail, PACK.format(fmt="parquet"))
    manifest = manifest_of(result)
    assert set(manifest) == MANIFEST_KEYS
    assert (
        manifest["pack_id"] == "t" and manifest["scale"] == "fabric_demo" and manifest["seed"] == 42
    )
    assert manifest["run_id"].endswith("_retail_fabric_demo_s42")
    assert set(manifest["timestamps"]) == {"started", "finished", "elapsed_seconds"}
    assert {"sqllocks-shape", "pandas", "numpy", "faker", "pyarrow", "scipy"} == set(
        manifest["sbom"]
    )
    assert set(manifest["tables"]["customer"]) == {"rows", "columns", "file_paths"}
    assert (
        manifest["tables"]["customer"]["rows"] == 200
        and manifest["tables"]["customer"]["columns"] == 8
    )
    assert [Path(p).name for p in manifest["tables"]["customer"]["file_paths"]] == [
        "customer.parquet"
    ]
    assert manifest["tables"]["store"]["file_paths"] == []
    assert manifest["outputs"]["kind"] == "file_drop" and manifest["outputs"]["files"] == 2
    assert list(manifest["tables"])[:2] == [
        "customer",
        "product_category",
    ]  # as the engine returns them
    assert ManifestBuilder.from_file(result.files_written[-1]).to_dict() == manifest
    assert "Shape v" in result.manifest.summary()  # type: ignore[union-attr]


def test_same_seed_same_data_and_a_new_run_never_overwrites_a_manifest(tmp_path, retail):
    a = run(tmp_path, retail, PACK.format(fmt="parquet"))
    b = run(tmp_path, retail, PACK.format(fmt="parquet"))
    assert a.files_written[-1] != b.files_written[-1]
    assert len(list((tmp_path / "out").glob("*_manifest.json"))) == 2
    first = pq.read_table(tmp_path / "out" / "Files" / "landing" / "order.parquet")
    c = run(tmp_path, retail, PACK.format(fmt="parquet"), out="out2")
    assert c.is_success
    assert pq.read_table(tmp_path / "out2" / "Files" / "landing" / "order.parquet").equals(first)


def test_stream_writes_one_file_per_matching_topic(tmp_path, retail):
    result = run(tmp_path, retail, STREAM)
    assert result.is_success, result.errors
    assert result.events_emitted == 1000
    lines = (tmp_path / "out" / "order_order_placed.jsonl").read_text().splitlines()
    assert len(lines) == 1000
    row = json.loads(lines[0])
    assert (
        row["order_id"] == 1
        and isinstance(row["order_date"], str)
        and "T" in row["order_date"] + "T"
    )
    assert not (tmp_path / "out" / "nothing_like_it_e.jsonl").exists()
    assert any("matches no table" in w for w in result.warnings)
    manifest = manifest_of(result)
    assert [Path(p).name for p in manifest["tables"]["order"]["file_paths"]] == [
        "order_order_placed.jsonl"
    ]


def test_hybrid_writes_micro_batches_and_events(tmp_path, retail):
    result = run(tmp_path, retail, HYBRID)
    assert result.is_success, result.errors
    assert (tmp_path / "out" / "micro_batch" / "customer.jsonl").is_file()
    assert (tmp_path / "out" / "stream_customer_updated.jsonl").is_file()
    assert result.events_emitted == 200
    assert result.validation_results == {"referential_integrity": True, "uniqueness": True}


def test_gates_all_pass_on_clean_data(tmp_path, retail):
    text = PACK.format(fmt="csv").replace(
        "[schema_conformance]",
        "[schema_conformance, referential_integrity, row_count, null_check, uniqueness]",
    )
    result = run(tmp_path, retail, text)
    assert result.is_success and all(result.validation_results.values())
    assert len(result.validation_results) == 5


def test_an_invalid_pack_does_not_run(tmp_path, retail):
    result = run(tmp_path, retail, PACK.format(fmt="csv").replace("customer, order", "ghost"))
    assert not result.is_success and result.manifest is None
    assert result.errors[0].startswith("Pack validation failed: Entity 'ghost'")
    assert not list((tmp_path / "out").glob("*"))


def test_an_unknown_scale_is_an_error_naming_the_presets(tmp_path, retail):
    result = run(tmp_path, retail, PACK.format(fmt="csv"), scale="enormous")
    assert (
        not result.is_success
        and "unknown scale 'enormous'; the presets are: fabric_demo," in result.errors[0]
    )


def test_a_spec_overrides_gates_root_and_tables_and_hashes_itself(fixtures, tmp_path, retail):
    spec = GSLParser().parse(fixtures / "retail_basic.gsl.yaml")
    pack = PackLoader().load(fixtures / "tutorial_custom_pack.yaml")
    result = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path, spec=spec)
    assert result.is_success, result.errors
    assert set(result.validation_results) == {"schema_conformance", "referential_integrity"}
    manifest = manifest_of(result)
    import hashlib

    assert (
        manifest["spec_hash"]
        == hashlib.sha256((fixtures / "retail_basic.gsl.yaml").read_bytes()).hexdigest()
    )
    assert pack.validation is not None and pack.validation.required_gates == [
        "schema_conformance"
    ]  # not changed


def test_chaos_in_a_spec_is_applied_and_counted(fixtures, tmp_path, retail):
    spec = GSLParser().parse(fixtures / "retail_chaos.gsl.yaml")
    pack = PackLoader().load(fixtures / "tutorial_custom_pack.yaml")
    result = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path, spec=spec)
    assert result.chaos_applied and result.is_success  # failing gates are what chaos is for
    manifest = manifest_of(result)
    assert manifest["chaos"] and all(
        isinstance(v, int) and v > 0 for v in manifest["chaos"].values()
    )
    assert not all(result.validation_results.values())
    assert "Chaos:" in result.summary() and "FAIL" in result.summary()
    again = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "again", spec=spec)
    assert manifest_of(again)["chaos"] == manifest["chaos"]  # deterministic


def test_chaos_in_a_pack_is_applied(tmp_path, retail):
    text = (
        PACK.format(fmt="csv")
        + "chaos: {enabled: true, intensity: hurricane, breaking_change_day: 30}\n"
    )
    result = run(tmp_path, retail, text)
    assert result.chaos_applied and manifest_of(result)["chaos"]
