"""Issue #50: the Excel source -- cell types kept, one table per sheet, and the findings that
``shape profile`` reports about a workbook."""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest

import shape
from shape.io import ReaderError, WorkbookError, open_source, open_workbook, read_table
from shape.io.excel import read_workbook, sheet_infos

from .conftest import Err, build_workbook


def _findings(prof, kind=None, column=None):
    out = list(prof.to_dict().get("findings", []))  # a one-sheet profile keeps all of them here
    if prof.is_dataset:
        for table in prof.tables.values():
            out += table.get("findings", [])
    return [
        f
        for f in out
        if (kind is None or f["kind"] == kind) and (column is None or f.get("column") == column)
    ]


# --- cell types ---------------------------------------------------------------------------------


def test_text_cells_keep_leading_zeros(workbook):
    path = workbook(
        {
            "Members": [
                ["zip", "ndc", "member_id"],
                ["02134", "0002-3227", "000123"],
                ["00501", "0002-3228", "007"],
            ]
        }
    )
    table = read_table(f"{path}#Members")
    assert table.schema.field("zip").type == pa.string()
    assert table.column("zip").to_pylist() == ["02134", "00501"]
    assert table.column("member_id").to_pylist() == ["000123", "007"]


def test_excel_types_come_through(workbook):
    rows = [
        ["i", "f", "b", "d", "dt", "s", "blank", "mixed"],
        [1, 1.5, True, dt.datetime(2024, 1, 2), dt.datetime(2024, 1, 2, 3, 4, 5), "x", None, 1],
        [
            2,
            2.5,
            False,
            dt.datetime(2024, 2, 3),
            dt.datetime(2024, 1, 3, 4, 5, 6),
            "y",
            None,
            "two",
        ],
        [None, None, None, None, None, None, None, True],
    ]
    table = read_table(f"{workbook({'T': rows})}#T")
    types = {f.name: f.type for f in table.schema}
    assert types["i"] == pa.int64() and types["f"] == pa.float64() and types["b"] == pa.bool_()
    assert types["d"] == pa.date32() and types["dt"] == pa.timestamp("us")
    assert types["s"] == pa.string() and types["blank"] == pa.string()
    assert types["mixed"] == pa.string()  # a column that mixes text and numbers is text
    assert table.column("i").to_pylist() == [1, 2, None]
    assert table.column("mixed").to_pylist() == ["1", "two", "TRUE"]
    assert table.column("blank").null_count == 3


def test_ragged_rows_and_a_leading_blank_row(workbook):
    path = workbook({"T": [[None, None], ["a", "b"], [1], [2, "x", "extra"]]})
    table = read_table(f"{path}#T")
    assert table.column_names == ["a", "b", "column_3"]
    assert table.num_rows == 2
    assert table.column("column_3").to_pylist() == [None, "extra"]


# --- sheets -------------------------------------------------------------------------------------


def test_one_table_per_visible_sheet(workbook):
    path = workbook(
        {"A": [["x"], [1]], "B": [["y"], [2], [3]], "Secret": [["z"], [9]]},
        hidden_sheets=["Secret"],
    )
    tables = open_workbook(path)
    assert list(tables) == ["A", "B"]
    assert tables["B"].table().num_rows == 2
    assert list(open_workbook(path, include_hidden=True)) == ["A", "B", "Secret"]
    assert [(i.name, i.state) for i in sheet_infos(path)][2] == ("Secret", "hidden")


def test_a_sheet_is_picked_with_a_fragment(workbook):
    path = workbook({"A": [["x"], [1]], "B": [["y"], [2]]})
    assert open_source(f"{path}#B").table().column_names == ["y"]
    assert open_source(f"{path}#B").name == "B"
    with pytest.raises(ReaderError, match="2 visible sheets"):
        open_source(str(path))
    with pytest.raises(WorkbookError, match="no sheet 'Nope'"):
        open_source(f"{path}#Nope")


def test_a_one_sheet_workbook_opens_as_that_sheet(workbook):
    path = workbook({"Only": [["x"], [1]]})
    assert open_source(str(path)).name == "Only"


# --- unreadable files ---------------------------------------------------------------------------


def test_xls_is_refused_with_a_clear_error(tmp_path):
    path = tmp_path / "old.xls"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    with pytest.raises(WorkbookError, match="legacy .xls"):
        shape.profile(str(path))
    with pytest.raises(WorkbookError, match="legacy .xls"):
        open_source(str(path))


def test_password_protected_workbook_is_refused(tmp_path):
    path = tmp_path / "locked.xlsx"
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)  # the encrypted wrapper
    with pytest.raises(WorkbookError, match="password-protected"):
        shape.profile(str(path))


def test_a_file_that_is_not_a_workbook(tmp_path):
    path = tmp_path / "fake.xlsx"
    path.write_text("a,b\n1,2\n")
    with pytest.raises(WorkbookError, match="not a valid .xlsx"):
        shape.profile(str(path))


# --- findings through shape.profile -------------------------------------------------------------


def test_profile_is_a_dataset_of_the_visible_sheets(workbook):
    path = workbook({"A": [["x"], [1], [2]], "B": [["y"], ["a"]]})
    prof = shape.profile(str(path))
    assert prof.is_dataset and list(prof.tables) == ["A", "B"]
    assert prof.name == "book"
    assert prof.tables["A"]["row_count"] == 2
    assert not _findings(prof)  # a clean workbook has none, and no `findings` key either
    assert "findings" not in prof.to_dict()
    assert all("findings" not in t for t in prof.tables.values())


def test_leading_zero_identifiers_survive_the_profile(workbook):
    path = workbook({"M": [["zip"], *[[f"{i:05d}"] for i in range(1, 60)]]})
    col = shape.profile(f"{path}#M").to_dict()["columns"]["zip"]
    assert col["dtype"] == "string"
    assert col["min_value"] == ["str", "00001"]
    assert col["string_length"]["min"] == 5


def test_numbers_stored_as_text_are_reported(workbook):
    rows = [["amount", "zip"], ["10", "02134"], ["20.5", "00501"], [3, "x"], ["1,200", "90210"]]
    (f,) = _findings(
        shape.profile(f"{workbook({'T': rows})}#T"), "numbers_stored_as_text", "amount"
    )
    assert f["count"] == 3 and f["share"] == 0.75  # the 3 is a real number
    assert f["cells"] == ["A2", "A3", "A5"] and f["examples"] == ["10", "20.5", "1,200"]
    assert f["table"] == "T"
    (z,) = _findings(
        shape.profile(f"{workbook({'T': rows}, name='z.xlsx')}#T"), "numbers_stored_as_text", "zip"
    )
    assert z["count"] == 3 and z["leading_zeros"] == 2 and z["kept_as_text"] is True
    assert z["share"] == 0.75  # three of the four values


def test_dates_stored_as_text_are_reported(workbook):
    rows = [
        ["d"],
        ["2024-01-31"],
        ["01/31/2024"],
        ["31-Jan-2024"],
        ["not a date"],
        [dt.datetime(2024, 1, 1)],
        ["99999999"],
    ]
    (f,) = _findings(shape.profile(f"{workbook({'T': rows})}#T"), "dates_stored_as_text")
    assert f["count"] == 3 and f["cells"] == ["A2", "A3", "A4"]
    assert f["share"] == pytest.approx(3 / 6)


def test_hidden_columns_are_reported_and_still_read(workbook):
    path = workbook({"T": [["a", "b", "c"], [1, 2, 3]]}, hidden_columns={"T": ["B"]})
    prof = shape.profile(f"{path}#T")
    (f,) = _findings(prof, "hidden_column")
    assert (f["column"], f["column_letter"], f["table"]) == ("b", "B", "T")
    assert list(prof.to_dict()["columns"]) == ["a", "b", "c"]


def test_hidden_sheets_are_reported_and_read_only_on_request(workbook):
    path = workbook(
        {"A": [["x"], [1]], "H": [["y"], [2]], "V": [["z"], [3]]},
        hidden_sheets=["H"],
        very_hidden=["V"],
    )
    prof = shape.profile(str(path))
    assert list(prof.tables) == ["A"]
    hidden = {f["sheet"]: f for f in _findings(prof, "hidden_sheet")}
    assert {n: f["state"] for n, f in hidden.items()} == {"H": "hidden", "V": "veryHidden"}
    assert not any(f["read"] for f in hidden.values())
    both = shape.profile(str(path), include_hidden=True)
    assert list(both.tables) == ["A", "H", "V"]
    assert all(f["read"] for f in _findings(both, "hidden_sheet"))
    only = shape.profile(f"{path}#H")  # naming a hidden sheet reads it
    assert only.name == "H" and only.to_dict()["row_count"] == 1
    assert [f["read"] for f in _findings(only, "hidden_sheet") if f["sheet"] == "H"] == [True]


def test_error_cells_are_reported_by_code(workbook):
    codes = ["#REF!", "#N/A", "#VALUE!", "#DIV/0!", "#NAME?", "#NUM!", "#NULL!"]
    rows = [["v"], *[[Err(c)] for c in codes], [1], [2], ["#N/A"]]  # the last is plain text
    prof = shape.profile(f"{workbook({'T': rows})}#T")
    (f,) = _findings(prof, "error_cells")
    assert f["count"] == 7 and f["by_error"] == dict.fromkeys(sorted(codes), 1)
    assert f["cells"][0] == "A2" and f["share"] == pytest.approx(7 / 10)
    assert prof.to_dict()["columns"]["v"]["null_count"] == 7  # an error cell is a missing value
    assert not _findings(prof, "sentinel_values", "v")  # the text "#N/A" is not an error


def test_duplicate_headers_are_renamed_and_reported(workbook):
    path = workbook({"T": [["id", "name", "name", "id", "name_2", None], [1, "a", "b", 2, "c", 9]]})
    table = read_table(f"{path}#T")
    assert table.column_names == ["id", "name", "name_3", "id_2", "name_2", "column_6"]
    assert read_table(f"{path}#T").column_names == table.column_names  # deterministic
    prof = shape.profile(f"{path}#T")
    dup = {f["column"]: f for f in _findings(prof, "duplicate_header")}
    assert {c: (f["original"], f["cell"]) for c, f in dup.items()} == {
        "name_3": ("name", "C1"),
        "id_2": ("id", "D1"),
    }
    (blank,) = _findings(prof, "blank_header")
    assert blank["column"] == "column_6" and blank["cell"] == "F1"


def test_sentinel_values_are_reported(workbook):
    rows = [
        ["zip", "qty", "end", "status", "ok"],
        ["00000", 99999, dt.datetime(9999, 12, 31), "N/A", "fine"],
        ["02134", 5, dt.datetime(2024, 1, 1), "active", "fine"],
        ["00000", 99999, "9999-12-31", "N/A", "fine"],
    ]
    prof = shape.profile(f"{workbook({'T': rows})}#T")
    found = {(f["column"], f["value"]): f for f in _findings(prof, "sentinel_values")}
    assert set(found) == {
        ("zip", "00000"),
        ("qty", "99999"),
        ("end", "9999-12-31"),
        ("status", "N/A"),
    }
    assert found[("zip", "00000")]["count"] == 2 and found[("zip", "00000")][
        "share"
    ] == pytest.approx(2 / 3)
    assert found[("end", "9999-12-31")]["cells"] == ["C2", "C4"]
    assert found[("qty", "99999")]["examples"] == [99999, 99999]


def test_mixed_type_columns_are_reported(workbook):
    prof = shape.profile(f"{workbook({'T': [['v'], [1], ['x'], [2.5]]})}#T")
    (f,) = _findings(prof, "mixed_types")
    assert f["types"] == {"float": 1, "int": 1, "str": 1} and f["stored_as"] == "string"


def test_findings_survive_the_artifact_and_the_summary(workbook, tmp_path):
    path = workbook({"A": [["x"], ["00000"]], "H": [["y"], [1]]}, hidden_sheets=["H"])
    prof = shape.profile(str(path))
    out = tmp_path / "p.shape"
    shape.save(prof, out)
    again = shape.load(out)
    assert again == prof and _findings(again, "sentinel_values")
    summary = prof.summary()
    assert summary["tables"]["A"]["findings"] and summary["findings"][0]["kind"] == "hidden_sheet"


def test_csv_profiles_have_no_findings(tmp_path):
    (tmp_path / "t.csv").write_text("a,b\n1,x\n2,y\n")
    prof = shape.profile(str(tmp_path / "t.csv"))
    assert "findings" not in prof.to_dict() and "findings" not in prof.summary()


def test_workbook_in_a_dict_of_sources(workbook):
    path = workbook({"A": [["k"], [1], [2]]})
    prof = shape.profile({"t": f"{path}#A"})
    assert list(prof.tables) == ["t"]


def test_sheet_options_need_a_workbook(tmp_path):
    (tmp_path / "t.csv").write_text("a\n1\n")
    with pytest.raises(ValueError, match="xlsx workbooks only"):
        shape.profile(str(tmp_path / "t.csv"), sheet="x")


# --- the command line ---------------------------------------------------------------------------


def _shape(*args, cwd):
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_profile_json_lists_findings(workbook, tmp_path):
    path = workbook({"A": [["zip", "n"], ["00000", "5"]], "H": [["y"], [1]]}, hidden_sheets=["H"])
    out, js = tmp_path / "b.shape", tmp_path / "b.json"
    done = _shape("profile", path, "-o", out, "--json", js, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    summary = json.loads(js.read_text())
    kinds = {f["kind"] for f in summary["tables"]["A"]["findings"]}
    assert {"sentinel_values", "numbers_stored_as_text"} <= kinds
    assert summary["findings"][0]["sheet"] == "H"
    done = _shape(
        "profile", path, "-o", tmp_path / "h.shape", "--sheet", "H", "--json", js, cwd=tmp_path
    )
    assert done.returncode == 0 and json.loads(js.read_text())["name"] == "H"
    done = _shape(
        "profile", path, "-o", tmp_path / "i.shape", "--include-hidden", "--json", js, cwd=tmp_path
    )
    assert list(json.loads(js.read_text())["tables"]) == ["A", "H"]


def test_cli_profile_reports_a_legacy_file_as_input_error(tmp_path):
    old = tmp_path / "old.xls"
    old.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64)
    done = _shape("profile", old, "-o", tmp_path / "o.shape", cwd=tmp_path)
    assert done.returncode != 0 and "legacy .xls" in (done.stderr + done.stdout)


def test_read_workbook_findings_hold_cell_positions(tmp_path):
    path = build_workbook(tmp_path / "w.xlsx", {"T": [["a"], [Err("#REF!")], ["7"]]})
    wb = read_workbook(path)
    kinds = [f["kind"] for f in wb.sheets["T"].findings]
    assert kinds == ["error_cells", "numbers_stored_as_text"]
    assert Path(wb.path).name == "w.xlsx"


# --- round trip: profile, then generate --from ----------------------------------------------------


def test_leading_zero_identifiers_survive_profile_and_generate_from(workbook, tmp_path):
    rows = [["member_id", "zip", "plan"]]
    for i in range(1, 201):
        rows.append([f"{i:07d}", f"{(i * 37) % 900 + 1:05d}", ("gold", "silver", "bronze")[i % 3]])
    path = workbook({"Members": rows})
    profile = tmp_path / "members.shape"
    done = _shape("profile", path, "-o", profile, cwd=tmp_path)
    assert done.returncode == 0, done.stderr
    out = tmp_path / "out"
    done = _shape(
        "generate", "--from", profile, "-f", "csv", "-o", out, "--seed", "7", cwd=tmp_path
    )
    assert done.returncode == 0, done.stderr
    import csv

    (generated,) = list(out.rglob("*.csv"))
    with open(generated, newline="", encoding="utf-8") as fh:
        records = list(csv.DictReader(fh))
    assert len(records) == 200
    assert all(len(r["member_id"]) == 7 and r["member_id"].isdigit() for r in records)
    assert all(len(r["zip"]) == 5 and r["zip"].isdigit() for r in records)
    assert any(r["zip"].startswith("0") for r in records)


def test_repeated_zip_codes_regenerate_as_zero_padded_text(workbook, tmp_path):
    zips = ["02134", "00501", "10001", "00001", "90210"]
    rows = [["zip", "amount"], *[[zips[i % 5], i] for i in range(300)]]
    profile = tmp_path / "z.shape"
    assert _shape("profile", workbook({"Z": rows}), "-o", profile, cwd=tmp_path).returncode == 0
    out = tmp_path / "out"
    done = _shape(
        "generate", "--from", profile, "-f", "csv", "-o", out, "--seed", "3", cwd=tmp_path
    )
    assert done.returncode == 0, done.stderr
    import csv

    (generated,) = list(out.rglob("*.csv"))
    with open(generated, newline="", encoding="utf-8") as fh:
        zip_codes = [r["zip"] for r in csv.DictReader(fh)]
    assert len(zip_codes) == 300 and all(len(z) == 5 and z.isdigit() for z in zip_codes)
    assert any(z.startswith("0") for z in zip_codes)


def test_an_archive_that_inflates_absurdly_is_refused(tmp_path, monkeypatch):
    import zipfile

    from shape.io import excel

    monkeypatch.setattr(excel, "MAX_PLAIN_BYTES", 1 << 20)

    path = tmp_path / "bomb.xlsx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("xl/worksheets/sheet1.xml", b"0" * (8 << 20))  # 8 MiB of zeros
    with pytest.raises(WorkbookError, match="inflates"):
        shape.profile(str(path))


def test_the_share_safe_form_carries_no_findings(workbook, tmp_path):
    path = workbook({"A": [["zip"], ["02134"], ["00000"]]})
    profile, safe = tmp_path / "a.shape", tmp_path / "a.safe.json"
    assert _shape("profile", path, "-o", profile, cwd=tmp_path).returncode == 0
    assert _shape("profile", "safe", profile, "-o", safe, cwd=tmp_path).returncode == 0
    text = safe.read_text()
    assert "02134" not in text and "findings" not in text  # raw cell values never leave
    assert _shape("profile", "validate", "--safe", safe, cwd=tmp_path).returncode == 0
