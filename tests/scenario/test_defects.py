"""P6-14: regression tests for the baseline defects Shape fixes (the allow-list PK-1..PK-9 in
benchmarks/vs_refengine/pack_1to1/pack_common.py). Each fails on a plain port of the baseline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.scenario import PackError, PackLoader, PackRunner
from tests.scenario.conftest import PACK, STREAM, write


def run(tmp_path, retail, text, **kw):
    pack = PackLoader().load(write(tmp_path / "p.yaml", text))
    return PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "out", **kw)


def manifest(result) -> dict:
    return json.loads(Path(result.files_written[-1]).read_text())


def test_pk1_a_table_lists_only_its_own_files(tmp_path, retail):
    text = PACK.format(fmt="parquet").replace("[customer, order]", "[order, order_line]")
    tables = manifest(run(tmp_path, retail, text))["tables"]
    assert [Path(p).name for p in tables["order"]["file_paths"]] == ["order.parquet"]
    assert [Path(p).name for p in tables["order_line"]["file_paths"]] == ["order_line.parquet"]


def test_pk1_a_directory_named_like_a_table_does_not_claim_every_file(tmp_path, retail):
    pack = PackLoader().load(write(tmp_path / "p.yaml", PACK.format(fmt="parquet")))
    result = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "store_product_out")
    assert manifest(result)["tables"]["store"]["file_paths"] == []
    assert manifest(result)["tables"]["product"]["file_paths"] == []


def test_pk4_an_unknown_gate_fails_and_fails_the_run(tmp_path, retail):
    result = run(
        tmp_path, retail, PACK.format(fmt="csv").replace("schema_conformance", "schema_conformanse")
    )
    assert result.validation_results == {"schema_conformanse": False}
    assert not result.is_success
    assert "unknown gate" in result.errors[0]
    assert "FAIL (" in result.summary()


def test_pk4_a_failed_gate_fails_the_run_without_chaos(tmp_path, retail):
    from shape.generation.engine import Engine
    from shape.scenario.runner import _run_gate

    generated = Engine(retail.schema, scale="fabric_demo", seed=1).generate()
    assert _run_gate("uniqueness", generated)[0]
    broken = generated.tables["customer"].set_column(
        0, "customer_id", generated.tables["customer"]["customer_id"].slice(0, 1).take([0] * 200)
    )
    from dataclasses import replace

    bad = replace(generated, tables={**generated.tables, "customer": broken})
    assert _run_gate("uniqueness", bad) == (False, "customer has duplicate primary keys")
    assert not _run_gate(
        "row_count", replace(bad, tables={**bad.tables, "store": bad.tables["store"].slice(0, 0)})
    )[0]
    assert not _run_gate(
        "referential_integrity",
        replace(bad, tables={**bad.tables, "customer": bad.tables["customer"].slice(0, 5)}),
    )[0]
    assert not _run_gate(
        "schema_conformance",
        replace(bad, tables={**bad.tables, "store": bad.tables["store"].drop(["store_name"])}),
    )[0]


@pytest.mark.parametrize("root", ["/tmp/shape_escape", "../escape", "a/../../escape"])
def test_pk5_a_landing_root_cannot_escape_the_output_directory(tmp_path, retail, root):
    text = PACK.format(fmt="csv").replace("Files/landing", root)
    result = run(tmp_path, retail, text)
    assert (
        not result.is_success
        and "must be a relative path inside the output directory" in result.errors[0]
    )
    assert not (tmp_path / "escape").exists() and not Path("/tmp/shape_escape").exists()


def test_pk5_a_symlinked_landing_directory_cannot_escape(tmp_path, retail):
    outside = tmp_path / "outside"
    outside.mkdir()
    out = tmp_path / "out"
    (out / "Files").mkdir(parents=True)
    (out / "Files" / "landing").symlink_to(outside, target_is_directory=True)
    result = run(tmp_path, retail, PACK.format(fmt="csv"))
    assert not result.is_success and "leaves the output directory" in result.errors[0]
    assert list(outside.iterdir()) == []


def test_pk5_a_topic_name_cannot_write_outside(tmp_path, retail):
    result = run(tmp_path, retail, STREAM.replace("name: order,", "name: ../order,"))
    assert not result.is_success and "not usable in a file name" in result.errors[0]
    assert not (tmp_path / "order_order_placed.jsonl").exists()


def test_pk6_malformed_documents_are_errors_not_crashes(tmp_path):
    for text in ("", "- 1\n", "file_drop: [a]\n", "pack_version: '1'\n"):
        with pytest.raises(PackError):
            PackLoader().load(write(tmp_path / "x.yaml", text))


def test_pk7_pack_chaos_is_applied_not_ignored(tmp_path, retail):
    clean = run(tmp_path, retail, PACK.format(fmt="csv"))
    text = (
        PACK.format(fmt="csv")
        + "chaos: {enabled: true, intensity: hurricane, breaking_change_day: 30}\n"
    )
    chaotic = run(tmp_path / "c", retail, text) if (tmp_path / "c").mkdir() is None else None
    assert manifest(clean)["chaos"] == {}
    assert chaotic is not None and manifest(chaotic)["chaos"]


def test_pk8_unknown_keys_and_unsimulated_features_are_warned_about(tmp_path, retail):
    text = PACK.format(fmt="csv") + "lateness_typo: 1\n"
    assert "Unknown key 'lateness_typo' is ignored" in run(tmp_path, retail, text).warnings


def test_pk9_stream_events_carry_iso_datetimes(tmp_path, retail):
    run(tmp_path, retail, STREAM)
    first = json.loads((tmp_path / "out" / "order_order_placed.jsonl").read_text().splitlines()[0])
    assert isinstance(first["order_date"], str) and first["order_date"][:4].isdigit()
    assert "-" in first["order_date"]


def test_the_spec_hash_is_filled_for_a_spec_run_and_empty_without_one(fixtures, tmp_path, retail):
    from shape.scenario import GSLParser

    pack = PackLoader().load(fixtures / "tutorial_custom_pack.yaml")
    plain = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "a")
    assert manifest(plain)["spec_hash"] == ""
    spec = GSLParser().parse(fixtures / "retail_basic.gsl.yaml")
    with_spec = PackRunner().run(pack, retail, "fabric_demo", 42, tmp_path / "b", spec=spec)
    assert len(manifest(with_spec)["spec_hash"]) == 64
