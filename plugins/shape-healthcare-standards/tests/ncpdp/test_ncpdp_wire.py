from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.ncpdp import (
    Layout,
    MappingError,
    WireError,
    map_claims,
    parse_transaction,
    write_transaction,
)

FS, GS, SS = b"\x1c", b"\x1d", b"\x1e"


def test_separators_and_structure(layout: Layout, tables: TableSet) -> None:
    rows = tables.rows("pharmacy_claim")
    data = write_transaction(rows[:1], layout, tables=tables)
    head = data.split(GS)[0]
    assert head == b"SYN1T101" + b"900001" + b"20240206"
    assert data.count(GS) == 1
    segs = data.split(GS)[1].split(SS)
    assert segs[0] == b""
    assert segs[1].startswith(b"X0S1" + FS + b"X1RIVERA" + FS + b"X219780412" + FS + b"X31")
    assert [s[:4] for s in segs[1:]] == [b"X0S1", b"X0S2", b"X0S3", b"X0S4"]
    assert FS + b"X8R" + FS in segs[2]  # mail_order False mapped
    assert FS + b"X6" + b"60000" + FS in segs[2]  # 3 implied decimals


def test_round_trip_all_claims(layout: Layout, tables: TableSet) -> None:
    rows = tables.rows("pharmacy_claim")
    assert [r["claim_status"] for r in rows] == ["paid", "rejected", "reversed"]
    data = write_transaction(rows, layout, tables=tables)
    assert data.count(GS) == 3  # a group separator starts each of the three transactions
    # the header count says 3 transactions
    parsed = parse_transaction(data, layout)
    assert parsed.transaction_code == "T1"
    assert parsed.header["tx_count"] == "03"
    assert len(parsed.transactions) == 3
    for row, tx in zip(rows, parsed.transactions, strict=True):
        vals = tx.decoded(layout, "T1")
        assert vals["S2"]["claim id"] == row["rx_claim_id"]
        assert vals["S2"]["product"] == row["ndc"]
        assert vals["S2"]["quantity"] == Decimal(str(row["quantity"]))
        assert vals["S2"]["days"] == row["days_supply"]
        assert vals["S3"]["ingredient"] == Decimal(str(row["ingredient_cost"]))
        member = tables.index("member", "member_id")[row["member_id"]]
        assert vals["S1"]["birth date"] == member["birth_date"]
        assert isinstance(vals["S1"]["birth date"], dt.date)
        s2 = tx.segment("S2")
        assert s2 is not None and s2.get("Y1") == row["rx_claim_id"]


def test_response_transactions(layout: Layout, tables: TableSet) -> None:
    rows = tables.rows("pharmacy_claim")
    data = write_transaction(rows, layout, "T2", tables=tables)
    parsed = parse_transaction(data, layout)
    assert parsed.transaction_code == "T2"
    got = [tx.decoded(layout, "T2")["R1"] for tx in parsed.transactions]
    assert [g["status"] for g in got] == ["A", "R", "V"]
    assert "reject" not in got[0] and got[1]["reject"] == "79"
    assert got[0]["plan paid"] == Decimal("1.00")
    assert got[0]["patient pay"] == Decimal("5.00")
    assert got[2]["plan paid"] == Decimal("4.00")
    assert [g["claim id"] for g in got] == ["RX-0001", "RX-0002", "RX-0003"]


def test_situational_segment_omitted_without_data(layout: Layout, tables: TableSet) -> None:
    row = dict(tables.rows("pharmacy_claim")[0])
    row["prescriber_npi"] = None
    mapped = map_claims([row], layout, tables=tables)
    assert [s.id for s in mapped.transactions[0].segments] == ["S1", "S2", "S3"]


def test_missing_required_value(layout: Layout, tables: TableSet) -> None:
    row = dict(tables.rows("pharmacy_claim")[0])
    row["rx_number"] = None
    with pytest.raises(MappingError, match="RX-0001.*X4.*required"):
        map_claims([row], layout, tables=tables)
    with pytest.raises(MappingError, match="no pharmacy claim rows"):
        map_claims([], layout)
    # without the member companion, the required member fields have no value
    with pytest.raises(MappingError, match="X1"):
        map_claims([tables.rows("pharmacy_claim")[0]], layout)


def test_value_too_long_and_bad_map(layout: Layout, tables: TableSet) -> None:
    row = dict(tables.rows("pharmacy_claim")[0])
    row["rx_number"] = "1" * 13
    with pytest.raises(MappingError, match="longer than max_length"):
        map_claims([row], layout, tables=tables)
    row = dict(tables.rows("pharmacy_claim")[0])
    row["claim_status"] = "pending"
    with pytest.raises(MappingError, match="not in the field's map"):
        map_claims([row], layout, "T2", tables=tables)


def test_control_character_in_value_rejected(layout: Layout, tables: TableSet) -> None:
    row = dict(tables.rows("pharmacy_claim")[0])
    row["rx_number"] = "A\x1cB"
    with pytest.raises(MappingError, match="control"):
        map_claims([row], layout, tables=tables)


def test_parse_errors(layout: Layout, tables: TableSet) -> None:
    data = write_transaction(tables.rows("pharmacy_claim")[:1], layout, tables=tables)
    with pytest.raises(WireError, match="header is"):
        parse_transaction(b"short", layout)
    with pytest.raises(WireError, match="not defined"):
        parse_transaction(data.replace(b"T101", b"QQ01", 1), layout)
    head, rest = data.split(GS, 1)
    with pytest.raises(WireError, match="between a group separator"):
        parse_transaction(head + GS + b"junk" + rest, layout)
    with pytest.raises(WireError, match="id field"):
        parse_transaction(data.replace(b"X0S1", b"ZZS1", 1), layout)
    with pytest.raises(WireError, match="shorter"):
        parse_transaction(head + GS + SS + b"X0S1" + FS + b"X", layout)


def test_custom_separators(layout_doc: dict[str, object], tables: TableSet) -> None:
    layout_doc["separators"] = {"segment": "7E", "group": "7C", "field": "7D"}
    lay = Layout.from_dict(layout_doc)
    data = write_transaction(tables.rows("pharmacy_claim")[:1], lay, tables=tables)
    assert b"\x1e" not in data and b"~" in data and b"|" in data and b"}" in data
    assert parse_transaction(data, lay).transaction_code == "T1"
