"""FHIR sinks and emitter: files, counts, determinism, plugin kit conformance."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest
from fhir.resources.R4B import get_fhir_model_class
from fhir.resources.R4B.bundle import Bundle
from shape_healthcare_standards.contract import ContractError
from shape_healthcare_standards.fhir.emitter import FhirEmitter
from shape_healthcare_standards.fhir.sink import FhirBundleSink, FhirNdjsonSink
from shape_healthcare_standards.testing import sample_tables

from shape.plugins import kit

TABLES = sample_tables()
EXPECTED = {
    "member": {"Patient": 4},
    "eligibility": {"Coverage": 4},
    "provider": {"Practitioner": 4, "Organization": 3},
    "medical_claim": {"Claim": 4, "ExplanationOfBenefit": 4},
    "pharmacy_claim": {"MedicationDispense": 3},
}


def batches(name: str) -> list[pa.RecordBatch]:
    return TABLES[name].to_batches()


def read_ndjson(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n") and "\r" not in text
    return [json.loads(line) for line in text.splitlines()]


def snapshot(directory: Path) -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in sorted(directory.iterdir())}


@pytest.mark.parametrize("table", list(EXPECTED))
def test_ndjson_sink_writes_one_file_per_type(tmp_path, table):
    out = tmp_path / "bulk"
    n = FhirNdjsonSink().write(str(out), table, batches(table), tables=TABLES)
    assert n == sum(EXPECTED[table].values())
    assert sorted(p.name for p in out.iterdir()) == sorted(f"{t}.ndjson" for t in EXPECTED[table])
    for rtype, count in EXPECTED[table].items():
        rows = read_ndjson(out / f"{rtype}.ndjson")
        assert len(rows) == count
        for r in rows:
            assert r["resourceType"] == rtype
            get_fhir_model_class(rtype).model_validate(r)


@pytest.mark.parametrize("table", list(EXPECTED))
def test_sinks_work_with_the_primary_table_alone(tmp_path, table):
    n = FhirNdjsonSink().write(str(tmp_path / "a"), table, batches(table))
    m = FhirBundleSink().write(str(tmp_path / "b"), table, batches(table))
    assert n == m == sum(EXPECTED[table].values())
    for p in (tmp_path / "a").iterdir():
        for r in read_ndjson(p):
            get_fhir_model_class(r["resourceType"]).model_validate(r)


def test_ndjson_is_deterministic_and_row_ordered(tmp_path):
    for d in ("one", "two"):
        FhirNdjsonSink().write(str(tmp_path / d), "medical_claim", batches("medical_claim"))
    assert snapshot(tmp_path / "one") == snapshot(tmp_path / "two")
    ids = [r["id"] for r in read_ndjson(tmp_path / "one" / "Claim.ndjson")]
    assert ids == TABLES["medical_claim"].column("claim_id").to_pylist()
    assert [p.name for p in (tmp_path / "one").iterdir() if p.name.startswith(".")] == []


def test_ndjson_several_tables_share_a_directory_and_utf8(tmp_path):
    out = tmp_path / "bulk"
    for table in ("member", "pharmacy_claim"):
        FhirNdjsonSink().write(f"file://{out}", table, batches(table))
    assert sorted(p.name for p in out.iterdir()) == ["MedicationDispense.ndjson", "Patient.ndjson"]
    member = TABLES["member"].to_pylist()[0] | {"last_name": "Muñoz"}
    t = pa.Table.from_pylist([member], schema=TABLES["member"].schema)
    FhirNdjsonSink().write(str(out), "member", t.to_batches())
    assert "Muñoz" in (out / "Patient.ndjson").read_text(encoding="utf-8")


def test_unmapped_or_unknown_table_is_a_contract_error(tmp_path):
    for table in ("claim_diagnosis", "drug_reference", "nope"):
        with pytest.raises(ContractError):
            FhirNdjsonSink().write(str(tmp_path / "x"), table, batches("claim_diagnosis"))
        with pytest.raises(ContractError):
            FhirBundleSink().write(str(tmp_path / "y"), table, batches("claim_diagnosis"))


def test_bundle_sink_chunks_and_validates(tmp_path):
    out = tmp_path / "bundles"
    n = FhirBundleSink().write(
        str(out), "medical_claim", batches("medical_claim"), tables=TABLES, bundle_size=3
    )
    assert n == 8
    assert [p.name for p in sorted(out.iterdir())] == [
        "bundle-000001.json",
        "bundle-000002.json",
        "bundle-000003.json",
    ]
    sizes = []
    for p in sorted(out.iterdir()):
        data = json.loads(p.read_text(encoding="utf-8"))
        b = Bundle.model_validate(data)
        assert b.type == "collection" and data["id"] == p.stem
        sizes.append(len(data["entry"]))
        for e in data["entry"]:
            assert e["fullUrl"].endswith(f"{e['resource']['resourceType']}/{e['resource']['id']}")
    assert sizes == [3, 3, 2]


def test_bundle_default_size_and_stale_files_removed(tmp_path):
    out = tmp_path / "bundles"
    FhirBundleSink().write(str(out), "member", batches("member"), bundle_size=1)
    assert len(list(out.iterdir())) == 4
    FhirBundleSink().write(str(out), "member", batches("member"))
    assert [p.name for p in out.iterdir()] == ["bundle-000001.json"]
    with pytest.raises(ValueError, match="bundle_size"):
        FhirBundleSink().write(str(out), "member", batches("member"), bundle_size=0)


def test_emitter_appends_one_line_per_resource(tmp_path):
    path = tmp_path / "events" / "fhir.ndjson"
    em = FhirEmitter()
    assert em.emit(str(path), iter(batches("member"))) == 4
    assert em.emit(str(path), iter(batches("medical_claim")), table="medical_claim") == 8
    rows = read_ndjson(path)
    assert [r["resourceType"] for r in rows] == ["Patient"] * 4 + [
        "Claim",
        "ExplanationOfBenefit",
    ] * 4
    assert em.emit(str(path), iter(batches("member"))) == 4
    assert len(read_ndjson(path)) == 16


def test_emitter_companion_tables_resolve_references(tmp_path):
    path = tmp_path / "e.ndjson"
    FhirEmitter().emit(
        str(path), iter(batches("medical_claim")), table="medical_claim", tables=TABLES
    )
    claim = next(r for r in read_ndjson(path) if r["id"] == "CLM-P-0002")
    assert claim["insurance"][0]["coverage"] == {"reference": "Coverage/E2"}
    assert len(claim["item"]) == 2


def test_emitter_rejects_unmapped_table(tmp_path):
    with pytest.raises(ContractError):
        FhirEmitter().emit(
            str(tmp_path / "e"), iter(batches("claim_diagnosis")), table="claim_diagnosis"
        )


def test_plugin_kit_conformance(tmp_path):
    kit.check_sink(FhirNdjsonSink(), str(tmp_path / "nd"), batches("member"), table="member")
    kit.check_sink(FhirBundleSink(), str(tmp_path / "bu"), batches("member"), table="member")
    kit.check_emitter(FhirEmitter(), str(tmp_path / "ev.ndjson"), batches("member"))
    assert FhirNdjsonSink().name == "fhir-ndjson" and FhirBundleSink().name == "fhir-bundle"
    assert FhirEmitter().name == "fhir"
