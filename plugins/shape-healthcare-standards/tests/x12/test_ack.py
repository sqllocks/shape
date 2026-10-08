"""277CA claim acknowledgment (005010X214): contract table, writer, sink, determinism, safety."""

from __future__ import annotations

import datetime as dt
import json
import os
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pytest
from shape_healthcare_standards import codes, contract
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.contract import ContractError
from shape_healthcare_standards.testing import sample_tables
from shape_healthcare_standards.x12.ack import AckOptions, build_acknowledgment
from shape_healthcare_standards.x12.core import EnvelopeOptions
from shape_healthcare_standards.x12.sink import X12Acknowledgment277CASink
from x12_validator import MAP_DIR, segments, validate

from shape.plugins import kit

HERE = Path(__file__).parent
GOLDEN = HERE / "golden"
SCHEMA_FIXTURE = HERE / "fixtures" / "claim_acknowledgment.schema.json"
D = dt.date


def write(tables, tmp_path: Path, **options) -> list[Path]:
    sink = X12Acknowledgment277CASink()
    batches = tables["claim_acknowledgment"].to_batches()
    sink.write(str(tmp_path), "claim_acknowledgment", batches, tables=tables, **options)
    return sorted(tmp_path.glob("*.x12"))


def with_ack(tables, rows: list[dict]):
    t = dict(tables)
    t["claim_acknowledgment"] = pa.Table.from_pylist(
        rows, schema=contract.contract_schema("claim_acknowledgment")
    )
    return t


def ack(claim_id="CLM-P-0001", **kw):
    row = dict(
        claim_id=claim_id,
        acknowledgment_date=D(2024, 3, 21),
        status_category_code="A2",
        status_code="20",
    )
    row.update(kw)
    return row


def build(tables, **kw) -> list[str]:
    return build_acknowledgment(TableSet(tables), AckOptions(), EnvelopeOptions(**kw))


def segs_of(text: str) -> list[list[str]]:
    return [s.split("*") for s in text.split("~") if s]


# --- 1. input contract -------------------------------------------------------------------------


def test_contract_table_columns_and_key():
    cols = {c.name: c for c in contract.CONTRACT["claim_acknowledgment"]}
    assert [n for n, c in cols.items() if c.required] == [
        "claim_id",
        "acknowledgment_date",
        "status_category_code",
        "status_code",
    ]
    assert list(cols) == [
        "claim_id",
        "acknowledgment_date",
        "status_category_code",
        "status_code",
        "line_number",
        "entity_identifier_code",
        "action_code",
        "reference_number",
        "received_date",
    ]
    assert cols["acknowledgment_date"].type == pa.date32()
    assert cols["line_number"].type == pa.int64()
    assert contract.KEYS["claim_acknowledgment"] == ("claim_id", "line_number")


def test_frozen_arrow_schema_fixture():
    frozen = json.loads(SCHEMA_FIXTURE.read_text())
    assert frozen["format"] == "shape.healthcare.contract-table"
    assert frozen["version"] == 1
    assert frozen["table"] == "claim_acknowledgment"
    now = [
        {"name": f.name, "type": str(f.type), "nullable": f.nullable}
        for f in contract.contract_schema("claim_acknowledgment")
    ]
    assert now == frozen["fields"]


def test_null_line_number_is_a_valid_claim_level_key_but_duplicates_are_not():
    schema = contract.contract_schema("claim_acknowledgment")
    t = contract.coerce(
        "claim_acknowledgment",
        pa.Table.from_pylist([ack(), ack(line_number=1), ack("CLM-P-0002")], schema=schema),
    )
    assert contract.check_keys("claim_acknowledgment", t) == []
    dup = contract.coerce(
        "claim_acknowledgment", pa.Table.from_pylist([ack(), ack()], schema=schema)
    )
    assert contract.check_keys("claim_acknowledgment", dup)
    nokey = contract.coerce(
        "claim_acknowledgment", pa.Table.from_pylist([ack(None)], schema=schema)
    )
    assert contract.check_keys("claim_acknowledgment", nokey)


def test_coerce_requires_the_four_required_columns():
    with pytest.raises(ContractError, match="status_code"):
        contract.coerce("claim_acknowledgment", pa.table({"claim_id": ["a"]}))


@pytest.mark.parametrize(
    ("fn", "good", "bad"),
    [
        (
            codes.is_claim_status_category,
            ["A1", "A2", "A8", "F0", "R3"],
            ["a1", "1A", "AA1", "", "A"],
        ),
        (codes.is_claim_status_code, ["1", "20", "65535"[:5]], ["", "123456", "2A", "-1"]),
        (codes.is_entity_identifier, ["PR", "QC", "1P", "MSC", "85"], ["P", "pr", "ABCD", "P*"]),
    ],
)
def test_code_shapes(fn, good, bad):
    assert all(fn(g) for g in good)
    assert not any(fn(b) for b in bad)


def test_sample_acknowledgments_meet_the_contract():
    tables = sample_tables()
    t = tables["claim_acknowledgment"]
    assert t.schema == contract.contract_schema("claim_acknowledgment")
    assert contract.check_keys("claim_acknowledgment", t) == []
    rows = t.to_pylist()
    claims = {c["claim_id"] for c in tables["medical_claim"].to_pylist()}
    lines = {(r["claim_id"], r["line_number"]) for r in tables["medical_claim_line"].to_pylist()}
    for r in rows:
        assert r["claim_id"] in claims
        if r["line_number"] is not None:
            assert (r["claim_id"], r["line_number"]) in lines
    assert any(r["line_number"] is None and r["status_category_code"] == "A2" for r in rows)
    assert any(r["line_number"] is None and r["status_category_code"] == "A3" for r in rows)
    assert any(r["line_number"] is not None for r in rows)
    assert len({r["acknowledgment_date"] for r in rows}) == 2


# --- 2. writer layout ---------------------------------------------------------------------------


def test_one_interchange_per_acknowledgment_date_with_hn_group_and_x214():
    files = build(sample_tables())
    assert len(files) == 2
    for text in files:
        s = segs_of(text)
        ids = [x[0] for x in s]
        assert s[ids.index("GS")][1] == "HN" and s[ids.index("GS")][8] == "005010X214"
        st = s[ids.index("ST")]
        assert st[1] == "277" and st[3] == "005010X214"
        assert ids.count("ST") == 1 and ids.count("SE") == 1
        bht = s[ids.index("BHT")]
        assert bht[1:3] == ["0085", "08"] and bht[6] == "TH"
    ics = [segs_of(t)[0][13] for t in files]
    assert ics == ["000000001", "000000002"]


def test_loop_and_segment_layout():
    (first, _second) = build(sample_tables())
    s = segs_of(first)
    ids = [x[0] for x in s]
    hls = [x for x in s if x[0] == "HL"]
    assert [h[3] for h in hls[:3]] == ["20", "21", "19"]
    assert all(h[3] == "PT" for h in hls[3:])
    assert hls[0][1:3] == ["1", ""] and hls[1][1:3] == ["2", "1"] and hls[2][1:3] == ["3", "2"]
    # 2200A
    a = ids.index("TRN")
    assert s[a][1] == "1"
    assert s[a + 1][:3] == ["DTP", "050", "D8"] and s[a + 2][:3] == ["DTP", "009", "D8"]
    assert s[a + 2][3] == "20240321"
    # 2200B: trace type 2, STC, totals
    b = ids.index("TRN", a + 1)
    assert s[b][1] == "2" and s[b + 1][0] == "STC"
    assert [x[:2] for x in s[b + 2 : b + 6]] == [
        ["QTY", "90"],
        ["QTY", "AA"],
        ["AMT", "YU"],
        ["AMT", "YY"],
    ]
    # 2200C uses QA / QC
    c = ids.index("TRN", b + 1)
    assert s[c][1] == "1"
    assert [x[:2] for x in s[c + 2 : c + 6]] == [
        ["QTY", "QA"],
        ["QTY", "QC"],
        ["AMT", "YU"],
        ["AMT", "YY"],
    ]
    # 2200D claim status tracking
    text = first
    assert "TRN*2*CLM-P-0001~" in text
    assert "REF*1K*PCN-0001~" in text
    assert "DTP*472*D8*20240205~" in text
    assert "REF*FJ*2~" in text


def test_stc_composites_dates_and_billed_amounts():
    (first, _) = build(sample_tables())
    assert "STC*A2:20:PR*20240321*WQ*300~" in first
    assert "STC*A3:21:PR*20240321*U*1200~" in first  # explicit rejected claim
    s = segs_of(first)
    line = [x for x in s if x[0] == "STC" and x[3] == "U" and x[1].startswith("A7")]
    assert line == [["STC", "A7:21:PR", "20240321", "U"]]  # STC04 is not used on a line
    c = [x for x in s if x[0] == "STC"][1]
    assert c == ["STC", "A1:19:PR", "", "WQ", "1950.5"]  # 2200C has no STC02


def test_service_period_uses_rd8_for_a_range():
    (_, second) = build(sample_tables())
    assert "DTP*472*RD8*20240610-20240614~" in second


def test_received_date_falls_back_to_the_acknowledgment_date():
    t = with_ack(sample_tables(), [ack(), ack("CLM-P-0002", received_date=D(2024, 3, 1))])
    (text,) = build(t)
    s = segs_of(text)
    dtp050 = [x for x in s if x[0] == "DTP" and x[1] == "050"]
    assert dtp050[0][3] == "20240301"  # earliest received date among the rows
    t = with_ack(sample_tables(), [ack()])
    (text,) = build(t)
    assert [x for x in segs_of(text) if x[0] == "DTP" and x[1] == "050"][0][3] == "20240321"


def test_entity_code_is_written_when_given_and_omitted_otherwise():
    t = with_ack(sample_tables(), [ack(entity_identifier_code="PR"), ack("CLM-P-0002")])
    (text,) = build(t)
    assert "STC*A2:20:PR*20240321*WQ*300~" in text
    assert "STC*A2:20*20240321*WQ*450.5~" in text


def test_patient_and_provider_names_come_from_companion_tables():
    (first, _) = build(sample_tables())
    assert "NM1*QC*1*RIVERA*ALEX*J***MI*SYN100001-00~" in first
    assert "NM1*85*" in first and "*XX*" in first


def test_companions_other_than_medical_claim_are_optional():
    t = sample_tables()
    keep = {k: t[k] for k in ("claim_acknowledgment", "medical_claim")}
    (text,) = build(with_ack(keep, [ack()]))
    assert "NM1*QC*1*UNKNOWN" in text and "NM1*85*2*BILLING PROVIDER" in text
    assert "SVC" not in text


# --- 3. action code rule and totals -------------------------------------------------------------


@pytest.mark.parametrize(
    ("category", "action"),
    [("A1", "WQ"), ("A2", "WQ"), ("A3", "U"), ("A6", "U"), ("A7", "U"), ("A8", "U")],
)
def test_action_code_is_derived_from_the_category(category, action):
    t = with_ack(sample_tables(), [ack(status_category_code=category)])
    (text,) = build(t)
    assert f"*20240321*{action}*300~" in text


def test_explicit_action_code_wins_over_the_derivation():
    t = with_ack(sample_tables(), [ack(status_category_code="A2", action_code="U")])
    (text,) = build(t)
    assert "*20240321*U*300~" in text


@pytest.mark.parametrize("category", ["A0", "A4", "A5", "E0", "R3", "F1"])
def test_unmapped_category_without_action_code_raises_naming_claim_and_column(category):
    t = with_ack(sample_tables(), [ack(status_category_code=category)])
    with pytest.raises(ContractError, match=r"CLM-P-0001.*action_code"):
        build(t)


def test_unmapped_category_with_explicit_action_code_is_written():
    t = with_ack(sample_tables(), [ack(status_category_code="R3", action_code="WQ")])
    (text,) = build(t)
    assert "STC*R3:20*20240321*WQ*300~" in text


def test_bad_explicit_action_code_raises():
    t = with_ack(sample_tables(), [ack(action_code="XX")])
    with pytest.raises(ContractError, match=r"CLM-P-0001.*action_code"):
        build(t)


def test_service_line_acknowledgment_must_reject():
    t = with_ack(
        sample_tables(), [ack("CLM-P-0002"), ack("CLM-P-0002", line_number=1, action_code="WQ")]
    )
    with pytest.raises(ContractError, match=r"CLM-P-0002.*line 1.*U"):
        build(t)
    t = with_ack(
        sample_tables(),
        [ack("CLM-P-0002"), ack("CLM-P-0002", line_number=1, status_category_code="A1")],
    )
    with pytest.raises(ContractError, match=r"CLM-P-0002.*line 1"):
        build(t)  # A1 derives WQ, which the 2220D loop cannot carry


def test_line_row_without_a_claim_level_row_raises():
    t = with_ack(sample_tables(), [ack("CLM-P-0002", line_number=1, status_category_code="A7")])
    with pytest.raises(ContractError, match=r"CLM-P-0002.*claim-level"):
        build(t)


def _totals(text: str) -> dict[str, dict[tuple[str, str], Decimal]]:
    """QTY / AMT values per trace loop: ``"B"`` for 2200B, ``"C"`` for 2200C."""
    out: dict[str, dict[tuple[str, str], Decimal]] = {}
    level = ""
    traces = 0
    for x in segs_of(text):
        if x[0] == "TRN":
            traces += 1
            level = {2: "B", 3: "C"}.get(traces, "")  # 2200A, 2200B, 2200C
        elif x[0] in ("QTY", "AMT") and level:
            out.setdefault(level, {})[(x[0], x[1])] = Decimal(x[2])
    return out


def _action(row: dict) -> str:
    derived = {"A1": "WQ", "A2": "WQ", "A3": "U"}
    return row["action_code"] or derived[row["status_category_code"]]


def test_totals_balance_with_claim_level_rows():
    tables = sample_tables()
    billed = {
        c["claim_id"]: Decimal(str(c["total_billed"])) for c in tables["medical_claim"].to_pylist()
    }
    rows = [r for r in tables["claim_acknowledgment"].to_pylist() if r["line_number"] is None]
    for text, day in zip(build(tables), (D(2024, 3, 21), D(2024, 6, 22)), strict=True):
        mine = [r for r in rows if r["acknowledgment_date"] == day]
        acc = [r for r in mine if _action(r) == "WQ"]
        rej = [r for r in mine if _action(r) == "U"]
        totals = _totals(text)
        for level, names in (("B", ("90", "AA")), ("C", ("QA", "QC"))):
            t = totals[level]
            assert t[("QTY", names[0])] == len(acc) and t[("QTY", names[1])] == len(rej)
            assert t[("AMT", "YU")] == sum((billed[r["claim_id"]] for r in acc), Decimal(0))
            assert t[("AMT", "YY")] == sum((billed[r["claim_id"]] for r in rej), Decimal(0))
        assert len(acc) + len(rej) == len(mine)


def test_summary_action_is_wq_when_any_claim_is_accepted_else_u():
    all_rejected = with_ack(sample_tables(), [ack(status_category_code="A3")])
    (text,) = build(all_rejected)
    s = segs_of(text)
    stcs = [x for x in s if x[0] == "STC"]
    assert stcs[0][3] == "U" and stcs[1][3] == "U"  # 2200B and 2200C
    mixed = with_ack(sample_tables(), [ack(), ack("CLM-P-0003", status_category_code="A3")])
    (text,) = build(mixed)
    stcs = [x for x in segs_of(text) if x[0] == "STC"]
    assert stcs[0][3] == "WQ" and stcs[1][3] == "WQ"


# --- 4. sink ------------------------------------------------------------------------------------


def test_sink_names_and_registration():
    sink = X12Acknowledgment277CASink()
    assert sink.name == "x12-277ca" and sink.primary == "claim_acknowledgment"
    from shape_healthcare_standards import sinks

    assert sinks.X12Acknowledgment277CASink is X12Acknowledgment277CASink
    pyproject = (HERE.parents[1] / "pyproject.toml").read_text()
    assert 'x12-277ca = "shape_healthcare_standards.sinks:X12Acknowledgment277CASink"' in pyproject


def test_sink_writes_numbered_atomic_files_and_returns_row_count(tmp_path):
    tables = sample_tables()
    n = X12Acknowledgment277CASink().write(
        str(tmp_path),
        "claim_acknowledgment",
        tables["claim_acknowledgment"].to_batches(),
        tables=tables,
    )
    assert n == tables["claim_acknowledgment"].num_rows
    assert sorted(p.name for p in tmp_path.iterdir()) == ["277CA-000001.x12", "277CA-000002.x12"]


def test_sink_rejects_other_tables(tmp_path):
    with pytest.raises(ContractError, match="claim_acknowledgment"):
        X12Acknowledgment277CASink().write(
            str(tmp_path), "member", sample_tables()["member"].to_batches()
        )


def test_medical_claim_is_required(tmp_path):
    t = {"claim_acknowledgment": sample_tables()["claim_acknowledgment"]}
    with pytest.raises(ContractError, match="medical_claim"):
        write(t, tmp_path)


def test_missing_claim_raises_naming_it(tmp_path):
    t = with_ack(sample_tables(), [ack("CLM-NOPE")])
    with pytest.raises(ContractError, match="CLM-NOPE"):
        write(t, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_missing_line_raises_naming_it(tmp_path):
    t = with_ack(
        sample_tables(),
        [ack("CLM-P-0002"), ack("CLM-P-0002", line_number=99, status_category_code="A7")],
    )
    with pytest.raises(ContractError, match=r"CLM-P-0002.*99"):
        write(t, tmp_path)


def test_line_rows_require_the_line_table(tmp_path):
    t = with_ack(
        sample_tables(),
        [ack("CLM-P-0002"), ack("CLM-P-0002", line_number=1, status_category_code="A7")],
    )
    del t["medical_claim_line"]
    with pytest.raises(ContractError, match="medical_claim_line"):
        write(t, tmp_path)


def test_missing_provider_or_member_rows_raise_when_the_table_is_given(tmp_path):
    t = sample_tables()
    t["member"] = t["member"].slice(0, 0)
    with pytest.raises(ContractError, match="SYN100001-00"):
        write(t, tmp_path)


@pytest.mark.parametrize(
    ("col", "value"),
    [
        ("status_category_code", "a2"),
        ("status_category_code", "A22"),
        ("status_code", "ABC"),
        ("status_code", "123456"),
        ("entity_identifier_code", "P"),
        ("entity_identifier_code", "PR*X"),
    ],
)
def test_malformed_codes_raise_naming_the_row(tmp_path, col, value):
    t = with_ack(sample_tables(), [ack(**{col: value})])
    with pytest.raises(ContractError, match=rf"CLM-P-0001.*{col}"):
        write(t, tmp_path)


def test_duplicate_keys_raise(tmp_path):
    t = with_ack(sample_tables(), [ack(), ack()])
    with pytest.raises(ContractError, match="duplicate"):
        write(t, tmp_path)


def test_empty_table_writes_nothing(tmp_path):
    t = with_ack(sample_tables(), [])
    assert write(t, tmp_path) == []


def test_plugin_kit_conformance(tmp_path):
    tables = sample_tables()

    class Bound(X12Acknowledgment277CASink):
        def write(self, uri, table, batches, **options):
            return super().write(uri, table, batches, tables=tables, **options)

    kit.check_sink(
        Bound(),
        str(tmp_path),
        tables["claim_acknowledgment"].to_batches(),
        table="claim_acknowledgment",
    )
    assert list(tmp_path.glob("277CA-*.x12"))


# --- 5. determinism and safety ------------------------------------------------------------------


def test_same_inputs_give_byte_identical_files(tmp_path):
    tables = sample_tables()
    a = write(tables, tmp_path / "a", created=dt.datetime(2024, 4, 1, 8, 30))
    b = write(tables, tmp_path / "b", created=dt.datetime(2024, 4, 1, 8, 30))
    assert [p.read_bytes() for p in a] == [p.read_bytes() for p in b]
    c = write(tables, tmp_path / "c", created=dt.datetime(2024, 4, 2, 8, 30))
    assert [p.read_bytes() for p in a] != [p.read_bytes() for p in c]


def test_row_order_does_not_change_the_output():
    tables = sample_tables()
    rows = tables["claim_acknowledgment"].to_pylist()
    reversed_t = with_ack(tables, list(reversed(rows)))
    assert build(tables) == build(reversed_t)


def test_delimiters_in_names_cannot_break_a_segment(tmp_path):
    t = sample_tables()
    members = t["member"].to_pylist()
    members[0]["last_name"] = "O*Br~ien:Smith^Jr"
    members[0]["first_name"] = "A*B"
    t["member"] = pa.Table.from_pylist(members, schema=t["member"].schema)
    providers = t["provider"].to_pylist()
    for p in providers:
        p["org_name"] = "Clinic*One~Two:Three^"
        p["last_name"] = "X*Y"
    t["provider"] = pa.Table.from_pylist(providers, schema=t["provider"].schema)
    rows = t["claim_acknowledgment"].to_pylist()
    rows[0]["reference_number"] = "R*E~F:G^H"
    t["claim_acknowledgment"] = pa.Table.from_pylist(rows, schema=t["claim_acknowledgment"].schema)
    files = write(t, tmp_path)
    for f in files:
        ok, errors = validate(f)
        assert ok, errors
    text = files[0].read_text()
    assert "O BR IEN SMITH JR" in text and "R E F G H" in text


def test_no_registry_lookup_is_made(monkeypatch):
    import socket

    def boom(*a, **k):  # pragma: no cover - failure path
        raise AssertionError("network used")

    monkeypatch.setattr(socket.socket, "connect", boom)
    assert build(sample_tables())


# --- validation and golden files ----------------------------------------------------------------


def test_the_x214_map_is_present_and_selected():
    assert os.path.exists(os.path.join(MAP_DIR, "277.5010.X214.xml"))
    maps = Path(MAP_DIR, "maps.xml").read_text()
    assert "005010X214" in maps and "277.5010.X214.xml" in maps


def test_every_sample_file_validates_with_pyx12_against_x214(tmp_path):
    files = write(sample_tables(), tmp_path)
    assert len(files) == 2
    for f in files:
        ok, errors = validate(f)
        assert ok, errors
        assert errors == []
        assert any(s.get_seg_id() == "STC" for s in segments(f))


def test_validator_is_live_on_a_broken_acknowledgment(tmp_path):
    (path, *_) = write(sample_tables(), tmp_path)
    import re

    bad = tmp_path / "bad.x12"
    bad.write_text(re.sub(r"SE\*\d+\*", "SE*3*", path.read_text()))
    ok, errors = validate(bad)
    assert not ok and errors
    wrong = tmp_path / "wrong.x12"
    wrong.write_text(path.read_text().replace("*WQ*", "*ZZ*"))
    ok, errors = validate(wrong)
    assert not ok and errors


def test_golden_files_match_byte_for_byte(tmp_path):
    files = write(sample_tables(), tmp_path)
    golden = sorted(GOLDEN.glob("277CA-*.x12"))
    assert [p.name for p in files] == [p.name for p in golden] and golden
    for out, expected in zip(files, golden, strict=True):
        assert out.read_bytes() == expected.read_bytes(), out.name


def test_claim_and_line_rejections_appear_with_action_u_and_totals_balance(tmp_path):
    (first, _) = write(sample_tables(), tmp_path)
    s = segs_of(first.read_text())
    claim_u = [x for x in s if x[0] == "STC" and x[3] == "U" and x[1].startswith("A3")]
    line_u = [x for x in s if x[0] == "STC" and x[3] == "U" and x[1].startswith("A7")]
    assert claim_u and line_u
    b = _totals(first.read_text())["B"]
    assert b[("QTY", "90")] == 2 and b[("QTY", "AA")] == 1
    assert b[("AMT", "YU")] == Decimal("750.5") and b[("AMT", "YY")] == Decimal("1200")
