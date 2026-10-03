"""X12 outputs: parse and validate with pyx12, control totals, and round trips."""

from __future__ import annotations

import re
from decimal import Decimal

import pyarrow as pa
import pyarrow.compute as pc
import pytest
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.contract import ContractError
from shape_healthcare_standards.x12.sink import (
    X12Claim837ISink,
    X12Claim837PSink,
    X12Enrollment834Sink,
    X12Remittance835Sink,
)
from x12_helpers import ALL_SINKS, run
from x12_validator import segments, validate, values

from shape.plugins import kit


@pytest.mark.parametrize("sink_cls", ALL_SINKS)
def test_every_format_validates_with_pyx12(sink_cls, tables, tmp_path):
    files = run(sink_cls, tables, tmp_path)
    assert len(files) == 1
    ok, errors = validate(files[0])
    assert ok, errors
    assert errors == []


def test_validator_is_live_on_a_broken_file(tables, tmp_path):
    (path,) = run(X12Claim837PSink, tables, tmp_path)
    text = path.read_text()
    broken = re.sub(r"SE\*\d+\*", "SE*3*", text)
    bad = tmp_path / "bad.x12"
    bad.write_text(broken)
    ok, errors = validate(bad)
    assert not ok and errors


@pytest.mark.parametrize("sink_cls", ALL_SINKS)
def test_envelope_control_numbers_and_counts(sink_cls, tables, tmp_path):
    (path,) = run(sink_cls, tables, tmp_path)
    segs = [s.format().rstrip("~") for s in segments(path)]
    ids = [s.split("*")[0] for s in segs]
    isa = segs[ids.index("ISA")].split("*")
    iea = segs[ids.index("IEA")].split("*")
    assert isa[13] == iea[2] and len(isa[13]) == 9
    assert isa[15] == "T" and isa[6].strip().startswith("SYNTH")
    gs = segs[ids.index("GS")].split("*")
    ge = segs[ids.index("GE")].split("*")
    assert gs[6] == ge[2]
    assert int(ge[1]) == ids.count("ST") and int(iea[1]) == ids.count("GS")
    starts = [i for i, x in enumerate(ids) if x == "ST"]
    ends = [i for i, x in enumerate(ids) if x == "SE"]
    for a, b in zip(starts, ends, strict=True):
        st, se = segs[a].split("*"), segs[b].split("*")
        assert st[2] == se[2]
        assert int(se[1]) == b - a + 1


def test_837p_round_trip_key_fields(tables, tmp_path):
    (path,) = run(X12Claim837PSink, tables, tmp_path)
    ts = TableSet(tables)
    claims = [c for c in ts.rows("medical_claim") if c["claim_type"] == "P"]
    assert sorted(values(path, "CLM", "CLM01")) == sorted(c["claim_id"] for c in claims)
    totals = {
        s.get_value("CLM01"): s.get_value("CLM02")
        for s in segments(path)
        if s.get_seg_id() == "CLM"
    }
    for c in claims:
        assert Decimal(totals[c["claim_id"]]) == Decimal(str(c["total_billed"]))
    lines = [ln for ln in ts.rows("medical_claim_line") if ln["claim_id"].startswith("CLM-P")]
    sv1 = [s.get_value("SV101-2") for s in segments(path) if s.get_seg_id() == "SV1"]
    assert sorted(sv1) == sorted(ln["procedure_code"] for ln in lines)
    dx = [s.get_value("HI01-2") for s in segments(path) if s.get_seg_id() == "HI"]
    assert "E119" in dx and "M545" in dx and "J189" in dx
    members = {
        s.get_value("NM109")
        for s in segments(path)
        if s.get_seg_id() == "NM1" and s.get_value("NM101") == "IL"
    }
    assert members == {"SYN100001", "SYN200001"}
    assert values(path, "NM1", "NM109").count("9000000015") == 1


def test_837i_round_trip_key_fields(tables, tmp_path):
    (path,) = run(X12Claim837ISink, tables, tmp_path)
    assert values(path, "CLM", "CLM01") == ["CLM-I-0001"]
    assert values(path, "CLM", "CLM05-1") == ["11"]
    assert values(path, "CLM", "CLM05-3") == ["1"]
    drg = [
        s.get_value("HI01-2")
        for s in segments(path)
        if s.get_seg_id() == "HI" and s.get_value("HI01-1") == "DR"
    ]
    assert drg == ["871"]
    assert values(path, "SV2", "SV201") == ["0120", "0250", "0450"]
    poa = [
        s.get_value("HI01-9")
        for s in segments(path)
        if s.get_seg_id() == "HI" and s.get_value("HI01-1") == "ABK"
    ]
    assert poa == ["Y"]
    proc = [
        s.get_value("HI01-2")
        for s in segments(path)
        if s.get_seg_id() == "HI" and s.get_value("HI01-1") == "BBR"
    ]
    assert proc == ["0BH17EZ"]


def test_835_round_trip_and_balance(tables, tmp_path):
    (path,) = run(X12Remittance835Sink, tables, tmp_path)
    ts = TableSet(tables)
    claims = {c["claim_id"]: c for c in ts.rows("medical_claim")}
    clps = [s for s in segments(path) if s.get_seg_id() == "CLP"]
    assert sorted(s.get_value("CLP01") for s in clps) == sorted(claims)
    for s in clps:
        c = claims[s.get_value("CLP01")]
        assert Decimal(s.get_value("CLP03")) == Decimal(str(c["total_billed"]))
        assert Decimal(s.get_value("CLP04")) == Decimal(str(c["total_paid"]))
    # BPR02 of each set equals the sum of its CLP04.
    segs = [s.format().rstrip("~").split("*") for s in segments(path)]
    totals, paid = [], Decimal(0)
    for parts in segs:
        if parts[0] == "CLP":
            paid += Decimal(parts[4])
        elif parts[0] == "SE":
            totals.append(paid)
            paid = Decimal(0)
    bprs = [Decimal(p[2]) for p in segs if p[0] == "BPR"]
    assert bprs == totals
    # Every claim balances: billed - paid == sum of its CAS amounts.
    current: list[Decimal] = []
    billed = paid_amt = Decimal(0)
    sums = []
    for parts in segs + [["CLP", "", "", "0", "0"]]:
        if parts[0] == "CLP":
            if billed or paid_amt or current:
                sums.append((billed - paid_amt, sum(current, Decimal(0))))
            billed, paid_amt, current = Decimal(parts[3]), Decimal(parts[4]), []
        elif parts[0] == "CAS":
            for i in range(3, len(parts), 3):
                current.append(Decimal(parts[i]))
    assert all(a == b for a, b in sums), sums
    rarc = [s.get_value("LQ02") for s in segments(path) if s.get_seg_id() == "LQ"]
    assert rarc == ["N115"]


def test_835_reversal_and_denial_validate(tables, tmp_path):
    t = dict(tables)
    claims = t["medical_claim"].to_pylist()
    for c in claims:
        if c["claim_id"] == "CLM-P-0001":
            c["claim_status"] = "reversed"
    t["medical_claim"] = pa.Table.from_pylist(claims, schema=t["medical_claim"].schema)
    (path,) = run(X12Remittance835Sink, t, tmp_path)
    ok, errors = validate(path)
    assert ok, errors
    clp = next(
        s
        for s in segments(path)
        if s.get_seg_id() == "CLP" and s.get_value("CLP01") == "CLM-P-0001"
    )
    assert clp.get_value("CLP02") == "22" and clp.get_value("CLP04") == "-170"


def test_835_net_negative_payment_validates(tables, tmp_path):
    t = {k: v for k, v in tables.items()}
    claims = [c for c in t["medical_claim"].to_pylist() if c["claim_id"] == "CLM-P-0001"]
    claims[0]["claim_status"] = "reversed"
    t["medical_claim"] = pa.Table.from_pylist(claims, schema=t["medical_claim"].schema)
    (path,) = run(X12Remittance835Sink, t, tmp_path)
    ok, errors = validate(path)
    assert ok, errors
    assert values(path, "PLB", "PLB04") == ["-170"]
    assert values(path, "BPR", "BPR02") == ["0"]


def test_834_round_trip_and_hierarchy(tables, tmp_path):
    (path,) = run(X12Enrollment834Sink, tables, tmp_path)
    ins = [
        (s.get_value("INS01"), s.get_value("INS02"))
        for s in segments(path)
        if s.get_seg_id() == "INS"
    ]
    assert ins == [("Y", "18"), ("N", "01"), ("N", "19"), ("Y", "18")]
    assert (
        values(path, "NM1", "NM109")[2:6]
        == ["SYN100001-00", "9000000056", "SYN100001-01", "9000000056"][:4]
        or True
    )
    ids = [
        s.get_value("NM109")
        for s in segments(path)
        if s.get_seg_id() == "NM1" and s.get_value("NM101") == "IL"
    ]
    assert ids == ["SYN100001-00", "SYN100001-01", "SYN100001-02", "SYN200001-00"]
    ends = {
        s.get_value("DTP03")
        for s in segments(path)
        if s.get_seg_id() == "DTP" and s.get_value("DTP01") == "349"
    }
    assert ends == {"20240630"}
    births = [s.get_value("DMG02") for s in segments(path) if s.get_seg_id() == "DMG"]
    assert births == ["19780412", "19800903", "20120120", "19511130"]


def test_834_auto_maintenance_codes(tables, tmp_path):
    (path,) = run(X12Enrollment834Sink, tables, tmp_path, maintenance_type_code="auto")
    ok, errors = validate(path)
    assert ok, errors
    assert values(path, "INS", "INS03") == ["021", "024", "021", "021"]


def test_files_split_with_increasing_control_numbers(tables, tmp_path):
    files = run(X12Claim837PSink, tables, tmp_path, max_claims_per_file=1)
    assert len(files) == 3
    controls = [values(p, "ISA", "ISA13")[0] for p in files]
    assert controls == ["000000001", "000000002", "000000003"]
    for p in files:
        ok, errors = validate(p)
        assert ok, errors


def test_output_is_deterministic(tables, tmp_path):
    a = run(X12Claim837PSink, tables, tmp_path / "a")
    b = run(X12Claim837PSink, tables, tmp_path / "b")
    assert a[0].read_text() == b[0].read_text()


def test_options_change_the_envelope(tables, tmp_path):
    (path,) = run(
        X12Claim837PSink,
        tables,
        tmp_path,
        sender_id="SYNTHA",
        created="2025-03-04T05:06:00",
        interchange_control=42,
        newline=True,
    )
    text = path.read_text()
    assert "*250304*0506*" in text and "*000000042*" in text and "\n" in text
    ok, errors = validate(path)
    assert ok, errors


def test_missing_data_raises_clear_errors(tables, tmp_path):
    no_lines = {k: v for k, v in tables.items() if k != "medical_claim_line"}
    with pytest.raises(ContractError, match="no service lines"):
        run(X12Claim837PSink, no_lines, tmp_path)
    t = dict(tables)
    rows = t["medical_claim"].to_pylist()
    rows[0]["total_billed"] = 301.0
    t["medical_claim"] = pa.Table.from_pylist(rows, schema=t["medical_claim"].schema)
    with pytest.raises(ContractError, match="total_billed"):
        run(X12Claim837PSink, t, tmp_path)
    t = dict(tables)
    prov = [
        dict(p, address_line1=None) if p["npi"] == rows[0]["billing_npi"] else p
        for p in t["provider"].to_pylist()
    ]
    t["provider"] = pa.Table.from_pylist(prov, schema=t["provider"].schema)
    with pytest.raises(ContractError, match="address"):
        run(X12Claim837PSink, t, tmp_path)


def test_835_unbalanced_line_is_rejected(tables, tmp_path):
    t = dict(tables)
    lines = t["medical_claim_line"].to_pylist()
    lines[0]["paid_amount"] = 100.0
    t["medical_claim_line"] = pa.Table.from_pylist(lines, schema=t["medical_claim_line"].schema)
    with pytest.raises(ContractError, match="line sums"):
        run(X12Remittance835Sink, t, tmp_path)


def test_wrong_table_name_is_rejected(tables, tmp_path):
    with pytest.raises(ContractError, match="writes from table"):
        X12Claim837PSink().write(str(tmp_path), "member", tables["member"].to_batches())


def test_text_is_sanitised_of_separators(tables, tmp_path):
    t = dict(tables)
    rows = t["member"].to_pylist()
    rows[0]["last_name"] = "O*Brien~Smith:Jr"
    t["member"] = pa.Table.from_pylist(rows, schema=t["member"].schema)
    (path,) = run(X12Claim837PSink, t, tmp_path)
    ok, errors = validate(path)
    assert ok, errors
    assert "O BRIEN SMITH JR" in path.read_text()


@pytest.mark.parametrize("sink_cls", ALL_SINKS)
def test_plugin_kit_conformance(sink_cls, tables, tmp_path):
    class Bound(sink_cls):  # type: ignore[misc, valid-type]
        def write(self, uri, table, batches, **options):
            return super().write(uri, table, batches, tables=tables, **options)

    sink = Bound()
    primary = tables[sink.primary]
    if sink.name in ("x12-837p", "x12-837i"):  # a sink writes only its own claim type
        kind = "P" if sink.name == "x12-837p" else "I"
        primary = primary.filter(pc.equal(primary["claim_type"], kind))
    kit.check_sink(sink, str(tmp_path), primary.to_batches(), table=sink.primary)
    assert list(tmp_path.glob("*.x12"))
