"""HUNT2-cli: #604, #608-#610, #611-#614."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main

HEAD = "format: shape-project\nversion: 1\n"
GATES = json.loads((Path(__file__).parents[1] / "quality" / "fixtures" / "gates.json").read_text())


@pytest.fixture(autouse=True)
def _here(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SHAPE_DEBUG", raising=False)


def _profile(tmp_path: Path, name: str, text: str) -> Path:
    csv = tmp_path / f"{name}.csv"
    csv.write_text(text, encoding="utf-8")
    out = tmp_path / f"{name}.shape"
    assert main(["profile", str(csv), "-o", str(out), "--no-project"]) == 0
    return out


# -- #604 ------------------------------------------------------------------------------------


def test_failed_json_report_leaves_the_old_file_and_names_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv = tmp_path / "big.csv"
    csv.write_text("x\n1e308\n-1e308\n5\n", encoding="utf-8")
    report = tmp_path / "rep.json"
    report.write_text('{"keep": "me"}', encoding="utf-8")
    code = main(["profile", str(csv), "-o", str(tmp_path / "b.shape"), "--json", str(report)])
    err = capsys.readouterr().err
    assert code == 2
    assert "rep.json" in err and "not a finite number" in err
    assert report.read_text(encoding="utf-8") == '{"keep": "me"}'


def test_json_report_of_finite_numbers_still_written(tmp_path: Path) -> None:
    csv = tmp_path / "ok.csv"
    csv.write_text("x\n1\n2\n", encoding="utf-8")
    report = tmp_path / "rep.json"
    assert main(["profile", str(csv), "-o", str(tmp_path / "o.shape"), "--json", str(report)]) == 0
    assert json.loads(report.read_text(encoding="utf-8"))["row_count"] == 2


# -- #608 ------------------------------------------------------------------------------------

BASE = "id,amount\n1,1\n2,\n3,3\n4,4\n"
LATER = "id,amount\n1,1\n2,\n3,\n4,\n"


@pytest.mark.parametrize(
    "flags",
    [
        ["--null-rate", "nan"],
        ["--threshold", "null_rate=nan"],
        ["--column-threshold", "amount:null_rate=nan"],
        ["--mean-shift-std", "NaN"],
    ],
)
def test_nan_threshold_flag_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], flags: list[str]
) -> None:
    a, b = _profile(tmp_path, "a", BASE), _profile(tmp_path, "b", LATER)
    capsys.readouterr()
    try:
        code = main(["diff", str(a), str(b), "--no-project", *flags])
    except SystemExit as exc:  # argparse refuses a bad flag value itself
        code = exc.code
    assert code == 2
    assert "must be a number" in capsys.readouterr().err


def test_a_real_threshold_still_detects_drift(tmp_path: Path, capsys) -> None:
    a, b = _profile(tmp_path, "a", BASE), _profile(tmp_path, "b", LATER)
    capsys.readouterr()
    assert main(["diff", str(a), str(b), "--no-project", "--null-rate", "0.01"]) == 0
    assert json.loads(capsys.readouterr().out)["drifted"] is True


@pytest.mark.parametrize("value", [".nan", ".NaN"])
def test_nan_threshold_in_shape_yml_is_invalid(tmp_path: Path, capsys, value: str) -> None:
    f = tmp_path / "shape.yml"
    f.write_text(
        HEAD + f"sources:\n  x:\n    path: d\n    thresholds:\n      null_rate: {value}\n",
        encoding="utf-8",
    )
    assert main(["project", "validate", str(f)]) == 2
    assert "sources.x.thresholds.null_rate" in capsys.readouterr().err


# -- #609 ------------------------------------------------------------------------------------


def test_init_with_unreadable_gitattributes_writes_nothing(tmp_path: Path, capsys) -> None:
    g = tmp_path / "g"
    g.mkdir()
    (g / ".gitattributes").write_bytes(b"\xff\xfe*.csv x\n")
    assert main(["init", str(g)]) == 2
    assert not (g / "shape.yml").exists() and not (g / "data").exists()
    assert ".gitattributes" in capsys.readouterr().err


def test_init_with_a_file_where_a_folder_goes_writes_nothing(tmp_path: Path, capsys) -> None:
    g = tmp_path / "g"
    g.mkdir()
    (g / "data").write_text("", encoding="utf-8")
    assert main(["init", str(g)]) == 2
    assert not (g / "shape.yml").exists()
    assert "data" in capsys.readouterr().err


@pytest.mark.parametrize("name", [".inf", ".5", ".nan", "1e3"])
def test_init_writes_a_valid_file_for_a_number_like_name(tmp_path: Path, name: str, capsys) -> None:
    g = tmp_path / "proj"
    assert main(["init", str(g), "--name", name]) == 0
    capsys.readouterr()
    assert main(["project", "validate", str(g / "shape.yml")]) == 0


# -- #610 ------------------------------------------------------------------------------------


def test_naming_a_source_is_not_a_missing_selection(tmp_path: Path, capsys) -> None:
    (tmp_path / "shape.yml").write_text(
        HEAD + "sources:\n  a: {path: data/a}\n  b: {path: data/b}\n", encoding="utf-8"
    )
    (tmp_path / "data" / "a").mkdir(parents=True)
    (tmp_path / "data" / "a" / "x.csv").write_text("id\n1\n", encoding="utf-8")
    assert main(["profile", "a", "-o", str(tmp_path / "a.shape")]) == 0
    assert "none was selected" not in capsys.readouterr().err
    assert main(["verify", "a"]) == 0
    assert "none was selected" not in capsys.readouterr().err
    # no hint at all: the note stays
    assert main(["profile", str(tmp_path / "data" / "a"), "-o", str(tmp_path / "c.shape")]) == 0
    assert "none was selected" in capsys.readouterr().err


# -- #611 ------------------------------------------------------------------------------------


@pytest.fixture
def scored(tmp_path: Path) -> tuple[Path, Path]:
    d = tmp_path / "data"
    d.mkdir()
    pq.write_table(
        pa.table({"id": [1, 2, 2, 4], "name": ["a", None, "c", "d"], "age": [30, 200, 40, None]}),
        d / "customer.parquet",
    )
    g = tmp_path / "gates.json"
    g.write_text(json.dumps(GATES))
    return d, g


def _owners(capsys: pytest.CaptureFixture[str]) -> dict[str, str | None]:
    doc = json.loads(capsys.readouterr().out)
    return {c["column"]: c["owner"] for c in doc["dimensions"]["completeness"]["checks"]}


PROJECT_OWNER = (
    HEAD + "sources:\n  customer:\n    path: data\n    columns:\n      name:\n"
    "        owner: data-team\n"
)


def test_scorecard_owners_from_a_valid_project_file(scored, tmp_path: Path, capsys) -> None:
    d, g = scored
    (tmp_path / "shape.yml").write_text(PROJECT_OWNER, encoding="utf-8")
    assert main(["scorecard", str(d), "--schema", str(g), "--json"]) == 0
    assert _owners(capsys)["name"] == "data-team"


def test_scorecard_project_flag_takes_a_file_and_no_project_ignores_it(
    scored, tmp_path: Path, capsys
) -> None:
    d, g = scored
    (tmp_path / "elsewhere").mkdir()
    f = tmp_path / "elsewhere" / "shape.yml"
    f.write_text(PROJECT_OWNER, encoding="utf-8")
    base = ["scorecard", str(d), "--schema", str(g), "--json"]
    assert main([*base, "--project", str(f)]) == 0
    assert _owners(capsys)["name"] == "data-team"
    assert main([*base, "--project", str(f.parent)]) == 0  # a folder still works
    assert _owners(capsys)["name"] == "data-team"
    (tmp_path / "shape.yml").write_text(PROJECT_OWNER, encoding="utf-8")
    assert main([*base, "--no-project"]) == 0
    assert _owners(capsys)["name"] is None


# -- #612 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["hello world", "", "-- only a comment\n"])
def test_from_ddl_without_a_table_is_an_error(tmp_path: Path, capsys, text: str) -> None:
    f = tmp_path / "x.sql"
    f.write_text(text, encoding="utf-8")
    assert main(["from-ddl", str(f)]) == 2
    assert "no CREATE TABLE" in capsys.readouterr().err
    assert not (tmp_path / "x.gen.json").exists()


@pytest.mark.parametrize("encoding", ["utf-16", "utf-8-sig", "utf-32"])
def test_from_ddl_reads_a_script_with_a_byte_order_mark(tmp_path: Path, encoding: str) -> None:
    f = tmp_path / "x.sql"
    f.write_bytes("CREATE TABLE a (id INT PRIMARY KEY);\n".encode(encoding))
    assert main(["from-ddl", str(f)]) == 0
    assert "a" in json.loads((tmp_path / "x.gen.json").read_text())["tables"]


# -- #613 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("body", ["null", "[]", '"str"'])
def test_check_diff_plan_say_the_file_is_not_a_profile(tmp_path: Path, capsys, body: str) -> None:
    f = tmp_path / "n.json"
    f.write_text(body, encoding="utf-8")
    c = tmp_path / "c.json"
    c.write_text("{}", encoding="utf-8")
    for argv in (["check", str(f), str(c)], ["diff", str(f), str(f)], ["plan", str(f)]):
        assert main(argv) == 2
        err = capsys.readouterr().err
        assert "is not a Shape document" in err and "Error:" not in err, (argv, err)


# -- #614 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["nofile.yml", "folder"])
def test_project_validate_json_always_prints_json(tmp_path: Path, capsys, target: str) -> None:
    (tmp_path / "folder").mkdir()
    assert main(["project", "validate", target, "--json"]) == 2
    doc = json.loads(capsys.readouterr().out)
    assert doc["valid"] is False and doc["file"] == target and doc["problems"]


# -- #670 ------------------------------------------------------------------------------------


def test_continue_refuses_to_write_over_its_input(tmp_path: Path, capsys) -> None:
    data = tmp_path / "data"
    assert main(["generate", "retail", "--scale", "small", "-o", str(data), "--format", "csv"]) == 0
    before = {p.name: p.read_bytes() for p in data.iterdir()}
    capsys.readouterr()
    for out in (data, tmp_path / "data" / ".." / "data"):
        assert main(["continue", "retail", "--input", str(data), "-o", str(out)]) == 2
        assert "would overwrite the input" in capsys.readouterr().err
    assert {p.name: p.read_bytes() for p in data.iterdir()} == before


def test_continue_into_another_folder_still_works(tmp_path: Path, capsys) -> None:
    data = tmp_path / "data"
    assert main(["generate", "retail", "--scale", "small", "-o", str(data), "--format", "csv"]) == 0
    assert main(["continue", "retail", "--input", str(data), "-o", str(tmp_path / "next")]) == 0
    assert (tmp_path / "next" / "customer.csv").is_file()


# -- #672 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["profile", "capture", "learn", "dictionary", "from-ddl"])
def test_output_that_is_the_input_is_refused(tmp_path: Path, capsys, command: str) -> None:
    if command == "dictionary":
        csv = tmp_path / "d.csv"
        csv.write_text("id,amount\n1,5\n2,6\n", encoding="utf-8")
        target = tmp_path / "p.shape"
        assert main(["profile", str(csv), "-o", str(target), "--no-project"]) == 0
        argv = ["dictionary", str(target), "-o", str(target)]
    elif command == "from-ddl":
        target = tmp_path / "in.sql"
        target.write_text("CREATE TABLE a (id INT);\n", encoding="utf-8")
        argv = ["from-ddl", str(target), "-o", str(target)]
    else:
        target = tmp_path / "d.csv"
        target.write_text("id,amount\n1,5\n2,6\n", encoding="utf-8")
        argv = [command, str(target), "-o", str(target)]
        if command == "profile":
            argv.append("--no-project")
    before = target.read_bytes()
    capsys.readouterr()
    assert main(argv) == 2
    assert "output file is the input file" in capsys.readouterr().err
    assert target.read_bytes() == before


def test_output_next_to_the_input_is_not_refused(tmp_path: Path) -> None:
    csv = tmp_path / "d.csv"
    csv.write_text("id,amount\n1,5\n2,6\n", encoding="utf-8")
    assert main(["profile", str(csv), "-o", str(tmp_path / "d.shape"), "--no-project"]) == 0
    assert main(["capture", str(csv), "-o", str(tmp_path / "d.capture.json")]) == 0


# -- a column that is not in the file (key, fd, privacy-k) -------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["key", "n.csv", "zz"],
        ["key", "n.csv", "a", "zz"],
        ["privacy-k", "n.csv", "zz"],
        ["privacy-k", "n.csv", "a", "zz"],
        ["fd", "n.csv", "--determinant", "zz", "--dependent", "b"],
        ["fd", "n.csv", "--determinant", "a", "--dependent", "zz"],
    ],
)
def test_a_column_that_is_not_in_the_file_is_an_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    (tmp_path / "n.csv").write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "zz" in err and "not a column" in err and "a, b" in err


def test_existing_columns_still_measured(tmp_path: Path, capsys) -> None:
    (tmp_path / "n.csv").write_text("a,b\n1,x\n2,y\n", encoding="utf-8")
    assert main(["privacy-k", "n.csv", "a", "b"]) == 0
    assert json.loads(capsys.readouterr().out)["k"] == 1
    assert main(["key", "n.csv", "a"]) == 0
    assert main(["fd", "n.csv", "--determinant", "a", "--dependent", "b"]) == 0


# -- #681 ------------------------------------------------------------------------------------


@pytest.mark.parametrize("flag", ["--pii", "--exclude"])
def test_mask_options_naming_a_missing_column_are_refused(tmp_path: Path, capsys, flag) -> None:
    (tmp_path / "t.csv").write_text("id,email\n1,a@b.com\n", encoding="utf-8")
    value = "nosuch=email" if flag == "--pii" else "nosuch"
    assert main(["mask", "t.csv", "-o", "out", flag, value]) == 2
    err = capsys.readouterr().err
    assert "nosuch" in err and "not a column" in err and "id, email" in err
    assert not (tmp_path / "out").exists()


def test_mask_options_naming_a_real_column_still_work(tmp_path: Path) -> None:
    (tmp_path / "t.csv").write_text("id,email\n1,a@b.com\n", encoding="utf-8")
    assert main(["mask", "t.csv", "-o", "out", "--exclude", "id", "--pii", "email=email"]) == 0


@pytest.mark.parametrize("value", ["customer.nmae", "nosuchtable.name", "name"])
def test_scorecard_classified_must_name_a_column_of_the_data(scored, capsys, value) -> None:
    d, g = scored
    assert main(["scorecard", str(d), "--schema", str(g), "--classified", value]) == 2
    assert "--classified" in capsys.readouterr().err


def test_scorecard_classified_real_column_still_works(scored) -> None:
    d, g = scored
    assert main(["scorecard", str(d), "--schema", str(g), "--classified", "customer.name"]) == 0


# -- diff --only that matches no column ----------------------------------------------------


def test_diff_only_naming_no_column_is_refused(tmp_path: Path, capsys) -> None:
    a, b = _profile(tmp_path, "a", BASE), _profile(tmp_path, "b", LATER)
    capsys.readouterr()
    assert main(["diff", str(a), str(b), "--no-project", "--only", "nosuch"]) == 2
    err = capsys.readouterr().err
    assert "--only" in err and "nosuch" in err and "amount" in err
    assert main(["diff", str(a), str(b), "--no-project", "--only", "amount,nosuch"]) == 2


def test_diff_only_with_a_glob_or_real_column_still_works(tmp_path: Path, capsys) -> None:
    a, b = _profile(tmp_path, "a", BASE), _profile(tmp_path, "b", LATER)
    capsys.readouterr()
    for only in ("amount", "am*", "id,amount"):
        assert main(["diff", str(a), str(b), "--no-project", "--only", only]) == 0
        assert capsys.readouterr().out
