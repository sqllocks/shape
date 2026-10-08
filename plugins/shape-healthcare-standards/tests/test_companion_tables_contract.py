"""The ``tables`` option of the writers is a stable interface: these tests pin it.

Golden outputs live in ``tests/fixtures/companion_tables/<writer>/``. They are what the writers
wrote when the option was documented; a change to any byte is a break of the plugin's 1.x
promise. Regenerate only for a deliberate, documented change:
``python tests/test_companion_tables_contract.py``.
"""

from __future__ import annotations

import datetime as dt
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from shape_healthcare_standards import companion_tables, contract
from shape_healthcare_standards.contract import ContractError
from shape_healthcare_standards.emitters import FhirEmitter
from shape_healthcare_standards.sinks import (
    FhirBundleSink,
    FhirNdjsonSink,
    NcpdpSink,
    OmopSink,
    X12Acknowledgment277CASink,
    X12Claim837ISink,
    X12Claim837PSink,
    X12Enrollment834Sink,
    X12Remittance835Sink,
)
from shape_healthcare_standards.testing import sample_tables

HERE = Path(__file__).parent
FIXTURES = HERE / "fixtures" / "companion_tables"
LAYOUT = HERE / "ncpdp" / "synthetic_layout.json"
X12_OPTIONS = {"created": dt.datetime(2025, 1, 2, 3, 4)}


@dataclass(frozen=True)
class Writer:
    name: str
    primary: str
    make: Callable[[], Any]
    options: Mapping[str, Any] = field(default_factory=dict)
    emitter: bool = False

    def write(self, out: Path, batches: Any, tables: Any, *, with_option: bool = True) -> None:
        kw: dict[str, Any] = dict(self.options)
        if with_option:
            kw["tables"] = tables
        if self.emitter:
            self.make().emit(str(out / "events.ndjson"), batches, table=self.primary, **kw)
        else:
            self.make().write(str(out), self.primary, batches, **kw)


WRITERS = [
    Writer("x12-837p", "medical_claim", X12Claim837PSink, X12_OPTIONS),
    Writer("x12-837i", "medical_claim", X12Claim837ISink, X12_OPTIONS),
    Writer("x12-835", "medical_claim", X12Remittance835Sink, X12_OPTIONS),
    Writer("x12-834", "member", X12Enrollment834Sink, X12_OPTIONS),
    Writer("x12-277ca", "claim_acknowledgment", X12Acknowledgment277CASink, X12_OPTIONS),
    Writer("fhir-ndjson", "medical_claim", FhirNdjsonSink),
    Writer("fhir-bundle", "medical_claim", FhirBundleSink),
    Writer("omop", "medical_claim", OmopSink),
    Writer("ncpdp", "pharmacy_claim", NcpdpSink, {"layout": str(LAYOUT)}),
    Writer("fhir", "medical_claim", FhirEmitter, emitter=True),
]
IDS = [w.name for w in WRITERS]


def companions(w: Writer) -> dict[str, pa.Table]:
    return {k: v for k, v in sample_tables().items() if k != w.primary}


def primary_batches(w: Writer) -> list[pa.RecordBatch]:
    return sample_tables()[w.primary].to_batches()


def run(w: Writer, out: Path, tables: Any, batches: Any = None, **kw: Any) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    w.write(out, primary_batches(w) if batches is None else batches, tables, **kw)
    return out


def files_of(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes()
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def outcome(w: Writer, out: Path, tables: Any, **kw: Any) -> Any:
    """The files written, or the error text when the write fails."""
    try:
        return files_of(run(w, out, tables, **kw))
    except Exception as exc:
        return f"{type(exc).__name__}: {exc}"


def golden(w: Writer) -> dict[str, bytes]:
    return files_of(FIXTURES / w.name)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_golden_output_is_byte_identical(w: Writer, tmp_path: Path) -> None:
    expected = golden(w)
    assert expected, "the fixture output is missing"
    assert files_of(run(w, tmp_path, companions(w))) == expected


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_primary_replaces_a_companion_of_the_same_name(w: Writer, tmp_path: Path) -> None:
    stale = sample_tables()[w.primary].slice(0, 1)
    tables = {**companions(w), w.primary: stale}
    assert files_of(run(w, tmp_path, tables)) == golden(w)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_zero_batches_use_the_companion_of_the_primary_name(w: Writer, tmp_path: Path) -> None:
    tables = {**companions(w), w.primary: sample_tables()[w.primary]}
    assert files_of(run(w, tmp_path, tables, batches=iter([]))) == golden(w)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_zero_batches_and_no_companion_of_the_primary_name_raises(
    w: Writer, tmp_path: Path
) -> None:
    with pytest.raises(ContractError, match=f"no rows and no columns.*{w.primary}"):
        run(w, tmp_path, companions(w), batches=iter([]))


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_unknown_table_name_raises_and_lists_the_contract_tables(w: Writer, tmp_path: Path) -> None:
    tables = {**companions(w), "not_a_table": sample_tables()["member"]}
    with pytest.raises(ContractError) as err:
        run(w, tmp_path, tables)
    message = str(err.value)
    assert "not_a_table" in message
    assert all(name in message for name in contract.TABLE_NAMES)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_a_missing_required_companion_raises_and_names_it(w: Writer, tmp_path: Path) -> None:
    required = [t for t in companion_tables(w.name)["required"] if t != w.primary]
    if w.name in ("fhir-ndjson", "fhir-bundle", "fhir", "ncpdp"):
        assert required == []  # nothing is required: the missing tables only drop detail
    else:
        assert required
    for name in required:
        tables = {k: v for k, v in companions(w).items() if k != name}
        with pytest.raises(ContractError, match=name):
            run(w, tmp_path / name, tables)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_tables_outside_both_lists_are_not_read(w: Writer, tmp_path: Path) -> None:
    listed = set(companion_tables(w.name)["required"]) | set(companion_tables(w.name)["optional"])
    for name in set(companions(w)) - listed:
        tables = {k: v for k, v in companions(w).items() if k != name}
        assert files_of(run(w, tmp_path / name, tables)) == golden(w)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_optional_tables_are_read(w: Writer, tmp_path: Path) -> None:
    """Dropping an optional table changes the output (FHIR: for some primary table)."""
    primaries = [w.primary]
    if w.name == "omop":
        primaries = ["member", "medical_claim"]
    if w.name.startswith("fhir"):
        primaries = ["member", "eligibility", "provider", "medical_claim", "pharmacy_claim"]
    changed: set[str] = set()
    for primary in primaries:
        pw = Writer(w.name, primary, w.make, w.options, w.emitter)
        full = companions(pw)
        base = outcome(pw, tmp_path / primary / "base", full)
        for name in companion_tables(w.name)["optional"]:
            if name == primary:
                continue
            less = {k: v for k, v in full.items() if k != name}
            if outcome(pw, tmp_path / primary / name, less) != base:
                changed.add(name)
    expected = set(companion_tables(w.name)["optional"])
    if w.name == "ncpdp":  # the layout decides: the synthetic one maps no drug_reference field
        expected -= {"drug_reference"}
    assert changed == expected


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_record_batches_and_tables_give_the_same_output(w: Writer, tmp_path: Path) -> None:
    as_batches = {k: v.combine_chunks().to_batches()[0] for k, v in companions(w).items()}
    assert files_of(run(w, tmp_path, as_batches)) == golden(w)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_extra_columns_are_ignored(w: Writer, tmp_path: Path) -> None:
    tables = {
        k: v.append_column("extra_col", pa.array(["x"] * v.num_rows, pa.string()))
        for k, v in companions(w).items()
    }
    assert files_of(run(w, tmp_path, tables)) == golden(w)


@pytest.mark.parametrize("w", WRITERS, ids=IDS)
def test_none_and_an_absent_option_behave_the_same(w: Writer, tmp_path: Path) -> None:
    with_none = outcome(w, tmp_path / "none", None)
    absent = outcome(w, tmp_path / "absent", None, with_option=False)
    assert with_none == absent


def test_an_empty_companion_is_present_not_missing(tmp_path: Path) -> None:
    """The 834 writer needs eligibility: an empty table is a table, so nothing is written."""
    w = WRITERS[3]
    assert w.name == "x12-834"
    tables = {**companions(w), "eligibility": sample_tables()["eligibility"].slice(0, 0)}
    assert files_of(run(w, tmp_path, tables)) == {}


def test_golden_fixtures_exist_for_every_writer() -> None:
    assert sorted(p.name for p in FIXTURES.iterdir()) == sorted(IDS)


def regenerate() -> None:
    for w in WRITERS:
        target = FIXTURES / w.name
        shutil.rmtree(target, ignore_errors=True)
        run(w, target, companions(w))


if __name__ == "__main__":
    regenerate()
    sys.stdout.write(f"wrote {FIXTURES}\n")
