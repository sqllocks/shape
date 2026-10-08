"""W6-04 deliverable 5: ``shape dictionary`` writes a data dictionary from a profile and the
project file, in Markdown, HTML and JSON, and never prints a value of a CONFIDENTIAL column."""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest

import shape
from shape.cli.main import main

PROJECT = """\
format: shape-project
version: 1
sources:
  orders:
    path: data/orders.csv
    columns:
      amount:
        owner: finance-data@example.com
        annotations: {unit: EUR, note: "a | b <c>"}
      diagnosis:
        annotations: {classification: confidential}
  other:
    path: data/other.csv
"""

DIAGNOSES = ["asthma-zq", "diabetes-zq", "migraine-zq"]


def write_orders(path: Path, rows: int = 200) -> Path:
    lines = ["id,email,status,amount,diagnosis,note"]
    for i in range(rows):
        status = ["new", "paid", "paid", "shipped"][i % 4]
        note = "" if i % 5 == 0 else f"n{i}"
        lines.append(
            f"{i},user{i}@corp.example,{status},{(i % 40) * 1.5},{DIAGNOSES[i % 3]},{note}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    monkeypatch.chdir(tmp_path)
    csv = write_orders(tmp_path / "data" / "orders.csv")
    out = tmp_path / "orders.shape"
    # a full capture: the dictionary must withhold values the profile does hold (W1-11's safe
    # default would hold none of them)
    args = ["profile", str(csv), "-o", str(out), "--no-project", "--name", "orders"]
    assert main([*args, "--capture", "full"]) == 0
    (tmp_path / "shape.yml").write_text(PROJECT, encoding="utf-8")
    capsys.readouterr()
    return tmp_path


def run(work: Path, capsys, *args: str) -> tuple[int, str, str]:
    code = main(["dictionary", str(work / "orders.shape"), *args])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def build(work: Path, capsys, fmt: str, *args: str) -> str:
    out = work / f"dict.{fmt}"
    code, _, err = run(work, capsys, "--format", fmt, "-o", str(out), *args)
    assert code == 0, err
    return out.read_text(encoding="utf-8")


def document(work: Path, capsys, *args: str) -> dict:
    return json.loads(build(work, capsys, "json", *args))


def columns(doc: dict) -> dict[str, dict]:
    return {c["name"]: c for t in doc["tables"] for c in t["columns"]}


def test_json_document_declares_format_and_version(work, capsys):
    doc = document(work, capsys, "--source", "orders")
    assert doc["format"] == "shape-data-dictionary"
    assert doc["version"] == 1
    assert [t["name"] for t in doc["tables"]] == ["orders"]
    assert doc["tables"][0]["row_count"] == 200
    assert list(columns(doc)) == ["id", "email", "status", "amount", "diagnosis", "note"]


def test_every_column_has_the_listed_fields(work, capsys):
    cols = columns(document(work, capsys, "--source", "orders"))
    for col in cols.values():
        for key in (
            "name", "type", "null_rate", "distinct_estimate", "semantic", "classification",
            "numeric_range", "length_range", "format_pattern", "owner", "annotations",
        ):  # fmt: skip
            assert key in col, (col["name"], key)
    assert cols["note"]["null_rate"] == pytest.approx(0.2)
    assert cols["status"]["distinct_estimate"] == 3
    assert cols["status"]["length_range"] == {"min": 3, "max": 7}
    assert cols["status"]["classification"] == "INTERNAL"
    assert cols["email"]["format_pattern"] == "email"
    assert cols["email"]["semantic"]["label"] == "email"
    assert 0 < cols["email"]["semantic"]["confidence"] <= 1
    assert cols["status"]["semantic"] is None


def test_numeric_range_only_for_numbers_and_withheld_when_confidential(work, capsys):
    cols = columns(document(work, capsys, "--source", "orders"))
    assert cols["amount"]["numeric_range"] == {"min": 0, "max": 58.5}
    assert cols["status"]["numeric_range"] is None
    assert cols["id"]["classification"] == "CONFIDENTIAL"  # all distinct: the key backstop
    assert cols["id"]["numeric_range"] is None


def test_owner_and_annotations_come_from_the_project(work, capsys):
    cols = columns(document(work, capsys, "--source", "orders"))
    assert cols["amount"]["owner"] == "finance-data@example.com"
    assert cols["amount"]["annotations"] == {"unit": "EUR", "note": "a | b <c>"}
    assert cols["status"]["owner"] is None
    assert cols["status"]["annotations"] == {}


def test_project_is_found_from_the_working_folder_and_no_project_ignores_it(work, capsys):
    # no --source: the source named like the profile applies
    code, _, _ = run(work, capsys, "--format", "json", "-o", str(work / "a.json"))
    assert code == 0
    assert columns(json.loads((work / "a.json").read_text()))["amount"]["owner"]
    code, _, _ = run(work, capsys, "--no-project", "--format", "json", "-o", str(work / "b.json"))
    assert code == 0
    assert columns(json.loads((work / "b.json").read_text()))["amount"]["owner"] is None


def test_annotation_can_raise_the_classification_and_never_lower_it(work, capsys):
    cols = columns(document(work, capsys, "--source", "orders"))
    assert cols["diagnosis"]["classification"] == "CONFIDENTIAL"
    assert cols["email"]["classification"] == "CONFIDENTIAL"


def test_unknown_classification_label_fails_safe(work, capsys):
    (work / "shape.yml").write_text(
        PROJECT.replace("classification: confidential", "classification: bogus"),
        encoding="utf-8",
    )
    cols = columns(document(work, capsys, "--source", "orders"))
    assert cols["diagnosis"]["classification"] == "CONFIDENTIAL"


def test_no_example_or_top_value_without_the_flag(work, capsys):
    for fmt in ("md", "html", "json"):
        text = build(work, capsys, fmt, "--source", "orders")
        assert "shipped" not in text and "paid" not in text, fmt
    cols = columns(document(work, capsys, "--source", "orders"))
    assert all("examples" not in c and "top_values" not in c for c in cols.values())


def test_examples_for_columns_below_confidential_only(work, capsys):
    doc = document(work, capsys, "--source", "orders", "--examples")
    cols = columns(doc)
    assert doc["examples_included"] is True
    top = cols["status"]["top_values"]
    assert top[0] == {"value": "paid", "share": 0.5}
    assert {t["value"] for t in top} == {"new", "paid", "shipped"}
    assert 1 <= len(cols["status"]["examples"]) <= 3
    for name in ("id", "email", "diagnosis"):
        assert "examples" not in cols[name] and "top_values" not in cols[name], name


def profile_values(work: Path, names: tuple[str, ...]) -> set[str]:
    """Every value the profile holds for the columns ``names``: top values, enum values and the
    minimum and maximum."""
    found: set[str] = set()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        tables = shape.load(str(work / "orders.shape")).tables
    for table in tables.values():
        for name in names:
            col = table["columns"][name]
            found.update(str(v) for v in (col.get("value_counts_ext") or {}))
            found.update(str(v) for v in (col.get("enum_values") or {}))
            for key in ("min_value", "max_value"):
                if col.get(key):
                    found.add(str(col[key][1]))
    return found


@pytest.mark.parametrize("examples", [False, True])
@pytest.mark.parametrize("fmt", ["md", "html", "json"])
def test_confidential_values_never_appear(work, capsys, fmt, examples):
    secret = profile_values(work, ("email", "diagnosis"))
    assert {"asthma-zq", "user0@corp.example"} <= secret
    args = ["--source", "orders"] + (["--examples"] if examples else [])
    text = build(work, capsys, fmt, *args)
    leaked = sorted(v for v in secret if v in text)
    assert leaked == []
    if examples:
        assert "paid" in text  # the flag does work for the allowed columns


def test_output_is_byte_identical_for_the_same_inputs(work, capsys):
    for fmt in ("md", "html", "json"):
        first = (work / "x").with_suffix(f".{fmt}")
        second = (work / "y").with_suffix(f".{fmt}")
        for target in (first, second):
            code, _, err = run(
                work, capsys, "--source", "orders", "--examples", "--format", fmt,
                "-o", str(target),
            )  # fmt: skip
            assert code == 0, err
        assert first.read_bytes() == second.read_bytes(), fmt


def test_markdown_lists_each_table_and_column_and_escapes_cells(work, capsys):
    md = build(work, capsys, "md", "--source", "orders")
    assert md.startswith("# Data dictionary: orders")
    assert "## orders" in md
    for name in ("id", "email", "status", "amount", "diagnosis", "note"):
        assert f"`{name}`" in md
    assert "finance-data@example.com" in md
    assert "a \\| b <c>" in md
    assert md.endswith("\n")


def test_html_is_self_contained_and_escaped(work, capsys):
    html = build(work, capsys, "html", "--source", "orders")
    assert html.startswith("<!DOCTYPE html>")
    for forbidden in ("http://", "https://", "<script", "<link", "@import", "url(", " src="):
        assert forbidden not in html, forbidden
    assert "a | b &lt;c&gt;" in html and "<c>" not in html


def test_multi_table_dataset(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    folder = tmp_path / "ds"
    write_orders(folder / "orders.csv", rows=60)
    (folder / "refs.csv").write_text("code,label\n" + "\n".join(f"{i},l{i % 3}" for i in range(30)))
    out = tmp_path / "ds.shape"
    assert main(["profile", str(folder), "--dataset", "-o", str(out), "--no-project"]) == 0
    capsys.readouterr()
    target = tmp_path / "d.json"
    assert main(["dictionary", str(out), "--format", "json", "-o", str(target)]) == 0
    assert [t["name"] for t in json.loads(target.read_text())["tables"]] == ["orders", "refs"]


def test_all_null_column_has_no_ranges(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "t.csv").write_text("a,b\n" + "\n".join(f"{i}," for i in range(20)))
    assert main(["profile", str(tmp_path / "t.csv"), "-o", "t.shape", "--no-project"]) == 0
    assert main(["dictionary", "t.shape", "--format", "json", "-o", "d.json", "--examples"]) == 0
    b = columns(json.loads((tmp_path / "d.json").read_text()))["b"]
    assert b["null_rate"] == 1.0
    assert b["numeric_range"] is None and b["length_range"] is None
    assert b.get("top_values", []) == []


def test_output_is_written_where_asked_and_summarised(work, capsys):
    target = work / "sub" / "dict.md"
    code, out, _ = run(work, capsys, "--source", "orders", "-o", str(target))
    assert code == 0 and target.is_file()
    summary = json.loads(out)
    assert summary["format"] == "md" and summary["columns"] == 6 and summary["tables"] == 1


def test_missing_profile_exits_2(work, capsys):
    code = main(["dictionary", str(work / "nope.shape"), "-o", str(work / "d.md")])
    assert code == 2
    assert "not found" in capsys.readouterr().err
    assert not (work / "d.md").exists()


def test_unreadable_profile_exits_2(work, capsys):
    bad = work / "bad.shape"
    bad.write_bytes(b"not a zip")
    assert main(["dictionary", str(bad), "-o", str(work / "d.md")]) == 2
    capsys.readouterr()
    junk = work / "junk.json"
    junk.write_text("{}")
    assert main(["dictionary", str(junk), "-o", str(work / "d.md")]) == 2


def test_a_folder_is_not_a_profile(work, capsys):
    assert main(["dictionary", str(work), "-o", str(work / "d.md")]) == 2  # a folder


def test_source_not_in_the_project_exits_2(work, capsys):
    code, _, err = run(work, capsys, "--source", "nosuch", "-o", str(work / "d.md"))
    assert code == 2 and "nosuch" in err and "orders" in err
    assert not (work / "d.md").exists()


def test_source_without_a_project_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    write_orders(tmp_path / "o.csv", rows=20)
    assert main(["profile", "o.csv", "-o", "o.shape", "--no-project"]) == 0
    capsys.readouterr()
    assert main(["dictionary", "o.shape", "--source", "x", "-o", "d.md"]) == 2


def test_bad_format_exits_2(work, capsys):
    with pytest.raises(SystemExit) as e:
        main(["dictionary", str(work / "orders.shape"), "--format", "pdf", "-o", "x"])
    assert e.value.code == 2
