from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
from typing import Any

import pytest
from shape_healthcare_standards.ncpdp.layout import FieldFormat, Layout, LayoutError


def test_loads_synthetic_layout(layout: Layout) -> None:
    assert layout.header_length == 4 + 2 + 2 + 6 + 8
    assert layout.separators == {"segment": 0x1E, "group": 0x1D, "field": 0x1C}
    assert layout.transaction().code == "T1"
    assert layout.transaction("T2").kind == "response"
    with pytest.raises(LayoutError, match="no transaction"):
        layout.transaction("ZZ")


def _bad(doc: dict[str, Any], mutate: Any) -> LayoutError:
    d = copy.deepcopy(doc)
    mutate(d)
    with pytest.raises(LayoutError) as exc:
        Layout.from_dict(d)
    return exc.value


def test_bad_documents(layout_doc: dict[str, Any]) -> None:
    def seg_field(d: dict[str, Any]) -> dict[str, Any]:
        f: dict[str, Any] = d["transactions"]["T1"]["segments"][0]["fields"][0]
        return f

    cases: list[tuple[Any, str]] = [
        (lambda d: d.update(layout_format=2), "layout_format"),
        (lambda d: d.update(segment_id_field="X"), "segment_id_field"),
        (lambda d: d.update(bogus=1), "unknown key 'bogus'"),
        (
            lambda d: d.update(separators={"segment": "1E", "group": "1E", "field": "1C"}),
            "different",
        ),
        (lambda d: d.update(separators={"segment": "ZZ"}), "two hex digits"),
        (lambda d: d["header"][1].pop("role"), "transaction_code"),
        (lambda d: d["header"][0]["format"].update(max_length=9), "fixed width"),
        (lambda d: seg_field(d).pop("column"), "exactly one of"),
        (lambda d: seg_field(d).update(column="member.nope"), "not a column"),
        (lambda d: seg_field(d).update(column="other.last_name"), "alias"),
        (lambda d: seg_field(d).update(id="X10"), "exactly 2"),
        (lambda d: seg_field(d)["format"].update(type="blob"), "must be one of"),
        (lambda d: seg_field(d).update(usage="maybe"), "usage"),
        (lambda d: seg_field(d)["format"].update(decimals=2), "only applies"),
        (lambda d: d["transactions"]["T1"].update(kind="other"), "kind"),
        (lambda d: d["transactions"].update(BAD={"kind": "request", "segments": []}), "characters"),
        (lambda d: d["transactions"]["T1"]["segments"][1].update(id="S1"), "repeats"),
    ]
    for mutate, text in cases:
        assert text in str(_bad(layout_doc, mutate)), text


def test_all_problems_reported_together(layout_doc: dict[str, Any]) -> None:
    layout_doc["name"] = ""
    layout_doc["layout_format"] = 0
    with pytest.raises(LayoutError) as exc:
        Layout.from_dict(layout_doc)
    assert "2 problem(s)" in str(exc.value)


def test_not_an_object_and_bad_file(tmp_path: Any) -> None:
    with pytest.raises(LayoutError, match="JSON object"):
        Layout.from_dict([])
    p = tmp_path / "x.json"
    p.write_text("{nope", encoding="utf-8")
    with pytest.raises(LayoutError, match="not valid JSON"):
        Layout.load(p)
    with pytest.raises(LayoutError, match="cannot read"):
        Layout.load(tmp_path / "missing.json")


def test_amount_and_number_formats() -> None:
    amt = FieldFormat("amount", 8, decimals=2)
    assert amt.encode(12.345) == "1234"  # half-even on 12.345 -> 12.34
    assert amt.encode(5.0) == "500"
    assert amt.encode(0.0) == "0"
    assert amt.decode("1234") == Decimal("12.34")
    explicit = FieldFormat("amount", 8, decimals=2, implied_decimal=False)
    assert explicit.encode(5.0) == "5.00"
    assert explicit.decode("5.00") == Decimal("5.00")
    padded = FieldFormat("amount", 8, decimals=2, zero_pad=True)
    assert padded.encode(1.5) == "00000150"


def test_signed_overpunch() -> None:
    f = FieldFormat("amount", 8, decimals=2, overpunch=True)
    assert f.encode(12.34) == "123D"
    assert f.encode(-12.34) == "123M"
    assert f.encode(0.0) == "{"
    assert f.decode("123D") == Decimal("12.34")
    assert f.decode("123M") == Decimal("-12.34")
    plain = FieldFormat("amount", 8, decimals=2)
    assert plain.encode(-1.0) == "-100"
    assert plain.decode("-100") == Decimal("-1.00")


def test_dates_and_errors() -> None:
    d = FieldFormat("date", 8)
    assert d.encode(dt.date(2024, 2, 6)) == "20240206"
    assert d.decode("20240206") == dt.date(2024, 2, 6)
    with pytest.raises(ValueError):
        d.encode("2024-02-06")
    with pytest.raises(ValueError):
        d.decode("2024-02-06")
    with pytest.raises(ValueError, match="longer"):
        FieldFormat("alphanumeric", 3).encode("abcd")
    with pytest.raises(ValueError, match="number"):
        FieldFormat("numeric", 3).encode("x")
    assert FieldFormat("alphanumeric", 5, uppercase=True).encode("ab") == "AB"
