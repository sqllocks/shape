from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pytest
from shape_healthcare_standards import testing
from shape_healthcare_standards.contract import ContractError
from shape_healthcare_standards.ncpdp import Layout, LayoutError, NcpdpSink, parse_transaction

from shape.plugins import kit

LAYOUT_PATH = Path(__file__).parent / "synthetic_layout.json"


def _batches() -> list[pa.RecordBatch]:
    return testing.sample_tables()["pharmacy_claim"].to_batches()


def _companions() -> dict[str, pa.Table]:
    t = testing.sample_tables()
    return {k: t[k] for k in ("member", "provider", "drug_reference")}


def test_writes_one_file_per_claim(tmp_path: Path, layout: Layout) -> None:
    sink = NcpdpSink(layout=LAYOUT_PATH)
    n = sink.write(str(tmp_path / "out"), "pharmacy_claim", iter(_batches()), tables=_companions())
    assert n == 3
    files = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert files == ["RX-0001.T1.ncpdp", "RX-0002.T1.ncpdp", "RX-0003.T1.ncpdp"]
    parsed = parse_transaction((tmp_path / "out" / "RX-0002.T1.ncpdp").read_bytes(), layout)
    assert len(parsed.transactions) == 1
    assert parsed.transactions[0].decoded(layout, "T1")["S2"]["claim id"] == "RX-0002"


def test_option_overrides_and_response(tmp_path: Path, layout: Layout) -> None:
    doc = json.loads(LAYOUT_PATH.read_text(encoding="utf-8"))
    sink = NcpdpSink()
    n = sink.write(
        f"file://{tmp_path}", "pharmacy_claim", iter(_batches()), layout=doc, transaction_code="T2"
    )
    assert n == 3
    data = (tmp_path / "RX-0002.T2.ncpdp").read_bytes()
    assert (
        parse_transaction(data, layout).transactions[0].decoded(layout, "T2")["R1"]["reject"]
        == "79"
    )
    assert not list(tmp_path.glob(".ncpdp-*"))


def test_layout_is_required_with_a_byo_message(tmp_path: Path) -> None:
    with pytest.raises(LayoutError, match="licensed by NCPDP"):
        NcpdpSink().write(str(tmp_path), "pharmacy_claim", iter(_batches()))
    assert not list(tmp_path.iterdir())


def test_wrong_table(tmp_path: Path) -> None:
    batches = testing.sample_tables()["member"].to_batches()
    with pytest.raises(ContractError, match="pharmacy_claim"):
        NcpdpSink(layout=LAYOUT_PATH).write(str(tmp_path), "member", iter(batches))


def test_missing_companion_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="required"):
        NcpdpSink(layout=LAYOUT_PATH).write(str(tmp_path), "pharmacy_claim", iter(_batches()))


def test_kit_check_sink(tmp_path: Path) -> None:
    class WithCompanions(NcpdpSink):
        def write(self, uri: str, table: str, batches, **options):  # type: ignore[no-untyped-def]
            options.setdefault("tables", _companions())
            return super().write(uri, table, batches, **options)

    kit.check_sink(
        WithCompanions(layout=LAYOUT_PATH), str(tmp_path), _batches(), table="pharmacy_claim"
    )
    assert len(list(tmp_path.glob("*.ncpdp"))) == 3
    assert NcpdpSink.name == "ncpdp" and NcpdpSink.schemes == ("file",)
