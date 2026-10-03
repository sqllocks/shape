"""Issue #51: the ``excel`` format writes one workbook, a sheet per table and a ``_README``."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest

openpyxl = pytest.importorskip("openpyxl")

from shape.builtins.sinks.workbook import (  # noqa: E402
    MAX_COLUMN_WIDTH,
    WorkbookTooLargeError,
    identifier_columns,
    valid_sheet_names,
    write_workbook,
)
from shape.generation.engine import Engine  # noqa: E402
from shape.generation.output import write_engine, write_result  # noqa: E402
from shape.io import open_workbook  # noqa: E402
from shape.io.excel import read_workbook, sheet_infos  # noqa: E402


def _retail(seed: int = 7):
    from shape.cli.generation import load_target

    return Engine(load_target("retail"), scale="small", seed=seed)


def _readme(path: Path) -> list[list[object]]:
    wb = openpyxl.load_workbook(path, read_only=True)
    try:
        return [list(r) + [None] * (7 - len(r)) for r in wb["_README"].iter_rows(values_only=True)]
    finally:
        wb.close()


def _text_of(rows) -> str:
    return "\n".join(str(c) for r in rows for c in r if c is not None)


# --- retail round trip ----------------------------------------------------------------------------


def test_retail_round_trips_through_a_workbook(tmp_path):
    engine = _retail()
    (path,) = write_engine(engine, "excel", tmp_path)
    assert path.name == "retail.xlsx"
    result = _retail().generate()
    names = [i.name for i in sheet_infos(path)]
    assert names == ["_README", *result.generation_order]  # dependency order, README first
    back = {n: s.table() for n, s in open_workbook(path, include_hidden=True).items()}
    assert "_README" in back  # a visible sheet like any other: it reads as a table too
    for name in result.generation_order:
        want, got = result.tables[name], back[name]
        assert got.column_names == want.column_names and got.num_rows == want.num_rows
        for col in want.column_names:
            assert _same(want.column(col), got.column(col)), (name, col)


def _same(want: pa.ChunkedArray, got: pa.ChunkedArray) -> bool:
    """Generated values against the values read back (Excel has one number type, no decimals, and
    keeps a date as a date or a timestamp)."""
    a, b = want.to_pylist(), got.to_pylist()
    for x, y in zip(a, b, strict=True):
        if x is None or y is None:
            if x is not y:
                return False
        elif isinstance(x, (int, float)) and not isinstance(x, bool):
            if abs(float(x) - float(y)) > 1e-9 * max(1.0, abs(float(x))):
                return False
        elif hasattr(x, "as_tuple"):  # Decimal
            if abs(float(x) - float(y)) > 1e-9:
                return False
        elif (
            isinstance(x, dt.datetime) and isinstance(y, dt.date) and not isinstance(y, dt.datetime)
        ):
            if x != dt.datetime.combine(y, dt.time()):
                return False
        elif isinstance(x, dt.datetime):  # a cell keeps a time to the millisecond
            if abs(x - y) > dt.timedelta(milliseconds=1):
                return False
        elif x != y:
            return False
    return True


def test_identifier_columns_stay_text_through_the_workbook(tmp_path):
    t = pa.table(
        {
            "member_id": ["0000123", "0004567", "0000089"],
            "zip": ["02134", "00501", "10001"],
            "name": ["Ann", "Bo", "Cy"],
            "qty": [1, 2, 3],
        }
    )
    path = tmp_path / "m.xlsx"
    write_workbook(path, {"members": t})
    back = read_workbook(path).sheets["members"].table
    assert back.column("member_id").to_pylist() == ["0000123", "0004567", "0000089"]
    assert back.column("zip").type == pa.string() and back.column("qty").type == pa.int64()
    assert identifier_columns(t) == ["member_id", "zip"]
    wb = openpyxl.load_workbook(path)
    ws = wb["members"]
    assert ws["A2"].number_format == "@" and ws["B2"].number_format == "@"
    assert ws["C2"].number_format != "@" and ws["D2"].data_type == "n"


# --- sheet names ----------------------------------------------------------------------------------


def test_sheet_names_are_valid_and_unique():
    names = valid_sheet_names(
        [
            "a" * 40,
            "a" * 41,
            "Orders",
            "orders",
            "bad[name]:*?/\\",
            "'quoted'",
            "_README",
            "_readme",
            "History",
            "",
        ]
    )
    sheets = list(names.values())
    assert all(1 <= len(n) <= 31 for n in sheets)
    assert not any(c in n for n in sheets for c in "[]:*?/\\")
    assert len({n.lower() for n in sheets}) == len(sheets)
    assert "_readme" not in {n.lower() for n in sheets}  # taken by the README sheet
    assert not any(n.startswith("'") or n.endswith("'") for n in sheets)
    assert names["orders"] == "orders_2" and names["a" * 40] == "a" * 31
    assert names["a" * 41].startswith("a" * 29) and names["a" * 41].endswith("_2")
    assert names["bad[name]:*?/\\"] == "bad_name______"


def test_the_mapping_is_in_the_readme(tmp_path):
    long = "customer_lifetime_value_history_" + "x" * 10
    t = pa.table({"a": [1]})
    path = tmp_path / "w.xlsx"
    sheets = write_workbook(path, {long: t, "a/b": t, "_README": t})
    rows = _readme(path)
    mapping = {r[0]: r[1] for r in rows if r and r[0] in sheets}
    assert mapping == sheets
    assert [i.name for i in sheet_infos(path)] == ["_README", *sheets.values()]


# --- the _README ----------------------------------------------------------------------------------


def test_readme_says_what_the_file_is(tmp_path):
    engine = _retail(seed=11)
    (path,) = write_engine(engine, "excel", tmp_path)
    rows = _readme(path)
    facts = {r[0]: r[1] for r in rows if len(r) > 1 and r[1] is not None}
    from shape import __version__

    assert facts["Shape version"] == __version__
    assert (
        facts["Seed"] == 11 and facts["Scale"] == "small" and facts["Domain / schema"] == "retail"
    )
    assert facts["Schema mode"] == "3nf"
    assert "Generated at (UTC)" in facts and isinstance(facts["Generation time (s)"], float)
    counts = {r[0]: r[2] for r in rows if len(r) > 4 and r[1] in engine.order}
    assert counts == {n: int(engine.row_counts[n]) for n in engine.order}
    assert facts["Total rows"] == sum(counts.values())
    assert "none" in _text_of(rows[-2:])  # no chaos, no drift


def test_readme_lists_the_planted_chaos(tmp_path):
    log = tmp_path / "_chaos_ground_truth.jsonl"
    run = {
        "record": "run",
        "log_version": 1,
        "tool": "shape.chaos",
        "seed": 5,
        "batch": 2,
        "corruptions": [{"kind": "duplicates", "rate": 0.1}],
        "changes": 3,
    }
    changes = [
        {
            "record": "change",
            "table": "orders",
            "kind": "negative_amounts",
            "scope": "row",
            "row": 1,
            "key": 2,
            "column": "amount",
            "before": 10.0,
            "after": -10.0,
        },
        {
            "record": "change",
            "table": "orders",
            "kind": "negative_amounts",
            "scope": "row",
            "row": 2,
            "key": 3,
            "column": "amount",
            "before": 4.0,
            "after": -4.0,
        },
        {
            "record": "change",
            "table": "orders",
            "kind": "duplicates",
            "scope": "row",
            "row": 3,
            "key": 1,
            "column": None,
            "before": None,
            "after": None,
        },
    ]
    log.write_text("\n".join(json.dumps(r) for r in (run, *changes)) + "\n")
    t = pa.table({"id": [1, 2, 3], "amount": [10.0, -10.0, -4.0]})
    path = tmp_path / "c.xlsx"
    write_workbook(path, {"orders": t}, chaos_log=log, meta={"seed": 5})
    text = _text_of(_readme(path))
    assert "Planted chaos" in text and "Changes logged" in text
    rows = _readme(path)
    assert ["Changes logged", 3] in [r[:2] for r in rows]
    assert ["orders", "negative_amounts", "amount", 2, 1, 10.0, -10.0] in [r[:7] for r in rows]
    assert ["orders", "duplicates", None, 1, 3, None, None] in [r[:7] for r in rows]
    assert "none" not in _text_of(rows[-3:])  # something was planted


def test_readme_lists_the_planted_drift(tmp_path):
    plan = {
        "start": "2026-01-01",
        "days": 10,
        "events": [
            {
                "id": "e1",
                "kind": "null_rate",
                "table": "orders",
                "column": "status",
                "start": "2026-01-05",
                "to": 0.3,
            },
            {
                "id": "e2",
                "kind": "new_category",
                "table": "orders",
                "column": "status",
                "start": 3,
                "end": 6,
                "value": "lost",
                "share": 0.02,
            },
        ],
    }
    path = tmp_path / "d.xlsx"
    write_workbook(path, {"orders": pa.table({"status": ["a"]})}, drift_plan=plan)
    rows = _readme(path)
    assert "Planted drift" in _text_of(rows)
    by_id = {r[0]: r for r in rows if r and r[0] in ("e1", "e2")}
    assert by_id["e1"][1:3] == ["null_rate", "orders.status"]
    assert json.loads(by_id["e2"][5]) == {"share": 0.02, "value": "lost"}


def test_chaos_log_and_drift_plan_from_files_and_the_cli(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text(
        json.dumps({"record": "run", "seed": 1, "batch": 0})
        + "\n"
        + json.dumps(
            {
                "record": "change",
                "table": "customer",
                "kind": "pii_fill",
                "row": 4,
                "column": "email",
                "before": "a@b.co",
                "after": "123-45-6789",
            }
        )
        + "\n"
    )
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"start": "2026-01-01", "days": 3, "events": []}))
    out = tmp_path / "out"
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "shape.cli.main",
            "generate",
            "retail",
            "--scale",
            "small",
            "-f",
            "excel",
            "-o",
            str(out),
            "--chaos-log",
            str(log),
            "--drift-plan",
            str(plan),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    text = _text_of(_readme(out / "retail.xlsx"))
    assert "pii_fill" in text and "email" in text and "Planted drift" in text


# --- formatting and limits ------------------------------------------------------------------------


def test_header_widths_freeze_and_date_formats(tmp_path):
    t = pa.table(
        {
            "name": ["x" * 200, "short"],
            "when": [dt.date(2024, 1, 2), dt.date(2024, 1, 3)],
            "at": [dt.datetime(2024, 1, 2, 3, 4, 5), None],
            "n": [1.5, 2.5],
        }
    )
    path = tmp_path / "f.xlsx"
    write_workbook(path, {"t": t})
    ws = openpyxl.load_workbook(path)["t"]
    assert ws.freeze_panes == "A2"
    header = ws["A1"]
    assert header.font.b and header.fill.start_color.rgb.endswith("305496")
    assert ws.column_dimensions["A"].width == MAX_COLUMN_WIDTH  # capped, not 200
    assert 8 <= ws.column_dimensions["D"].width <= MAX_COLUMN_WIDTH
    assert ws["B2"].number_format == "yyyy-mm-dd" and "yyyy" in ws["C2"].number_format
    assert ws["B2"].value == dt.datetime(2024, 1, 2)


def test_text_that_looks_like_a_formula_or_an_error_stays_text(tmp_path):
    t = pa.table({"v": ["=1+1", "#N/A", "+5", "@x", "plain"]})
    path = tmp_path / "t.xlsx"
    write_workbook(path, {"t": t})
    back = read_workbook(path).sheets["t"]
    assert back.table.column("v").to_pylist() == ["=1+1", "#N/A", "+5", "@x", "plain"]
    assert not [f for f in back.findings if f["kind"] == "error_cells"]


def test_a_table_beyond_the_sheet_limit_is_refused_before_writing(tmp_path, monkeypatch):
    from shape.builtins.sinks import workbook

    monkeypatch.setattr(workbook, "MAX_SHEET_ROWS", 5)
    path = tmp_path / "big.xlsx"
    ok, big = pa.table({"a": [1, 2]}), pa.table({"a": list(range(5))})
    with pytest.raises(WorkbookTooLargeError, match=r"table 'big' has 5 rows.*holds 4"):
        write_workbook(path, {"ok": ok, "big": big})
    assert not path.exists()
    write_workbook(path, {"ok": ok, "four": pa.table({"a": [1, 2, 3, 4]})})  # 4 + header fits


def test_the_excel_format_is_one_workbook_for_write_result(tmp_path):
    result = _retail().generate()
    paths = write_result(result, "excel", tmp_path)
    assert [p.name for p in paths] == ["retail.xlsx"]
    assert list(tmp_path.glob("*.xlsx")) == paths
