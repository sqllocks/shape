"""``shape scorecard`` (W3-06)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.quality.scorecard import DIMENSIONS

GATES = json.loads((Path(__file__).parent / "fixtures" / "gates.json").read_text())


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    pq.write_table(
        pa.table({"id": [1, 2, 2, 4], "name": ["a", None, "c", "d"], "age": [30, 200, 40, None]}),
        d / "customer.parquet",
    )
    pq.write_table(pa.table({"id": [1, 2, 3, 4], "customer_id": [1, 9, 2, 4]}), d / "order.parquet")
    g = tmp_path / "gates.json"
    g.write_text(json.dumps(GATES))
    return d, g


def test_json_output(data, capsys):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["format"] == "shape-scorecard"
    assert list(doc["dimensions"]) == list(DIMENSIONS)
    assert doc["dimensions"]["completeness"]["score"] == 93.75


def test_markdown_output_default(data, capsys):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("# Shape data quality scorecard")
    assert "| completeness |" in out


def test_output_file_in_chosen_format(data, tmp_path, capsys):
    d, g = data
    out = tmp_path / "card.json"
    assert main(["scorecard", str(d), "--schema", str(g), "--json", "-o", str(out)]) == 0
    assert json.loads(out.read_text())["version"] == 1
    md = tmp_path / "card.md"
    assert main(["scorecard", str(d), "--schema", str(g), "-o", str(md)]) == 0
    assert md.read_text().startswith("# Shape data quality scorecard")


def test_samples_option(data, capsys):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g), "--json", "--samples", "0"]) == 0
    assert json.loads(capsys.readouterr().out)["samples"] == []


def test_classified_option_redacts(data, capsys):
    d, g = data
    assert (
        main(
            ["scorecard", str(d), "--schema", str(g), "--json", "--classified", "order.customer_id"]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    orphan = [s for s in doc["samples"] if s["gate"] == "referential_integrity"][0]
    assert orphan["values"] == {"customer_id": "[redacted]"}
    assert (
        main(
            [
                "scorecard",
                str(d),
                "--schema",
                str(g),
                "--json",
                "--classified",
                "order.customer_id",
                "--show-classified",
            ]
        )
        == 0
    )
    doc = json.loads(capsys.readouterr().out)
    orphan = [s for s in doc["samples"] if s["gate"] == "referential_integrity"][0]
    assert orphan["values"] == {"customer_id": 9}


def test_flag_column_written_and_quarantine_untouched(data, tmp_path, capsys):
    d, g = data
    out = tmp_path / "flagged"
    assert main(["scorecard", str(d), "--schema", str(g), "--flag-output", str(out), "--json"]) == 0
    t = pq.read_table(out / "customer.parquet")
    assert t.column_names == ["id", "name", "age", "_shape_dq_failed"]
    assert t.column("_shape_dq_failed").to_pylist() == [False, True, True, False]
    assert not (tmp_path / "quarantine").exists()
    src = pq.read_table(d / "customer.parquet")
    assert src.column_names == ["id", "name", "age"]  # the input is never rewritten


def test_flag_column_name_and_csv(tmp_path, data, capsys):
    d, g = data
    csv_dir = tmp_path / "csv"
    csv_dir.mkdir()
    pacsv.write_csv(pq.read_table(d / "customer.parquet"), csv_dir / "customer.csv")
    pacsv.write_csv(pq.read_table(d / "order.parquet"), csv_dir / "order.csv")
    out = tmp_path / "flagged"
    assert (
        main(
            [
                "scorecard",
                str(csv_dir),
                "--schema",
                str(g),
                "--flag-output",
                str(out),
                "--flag-column",
                "bad",
                "--json",
            ]
        )
        == 0
    )
    assert "bad" in pacsv.read_csv(out / "customer.csv").column_names


def test_flag_output_cannot_be_input(data, capsys):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g), "--flag-output", str(d)]) == 2
    assert "overwrite" in capsys.readouterr().err


def test_suppressions_option(data, tmp_path, capsys):
    d, g = data
    s = tmp_path / "s.json"
    s.write_text(
        json.dumps(
            {
                "format": "shape-scorecard-suppressions",
                "version": 1,
                "entries": [
                    {
                        "action": "suppress",
                        "check": "referential_integrity",
                        "reason": "orphans known",
                    }
                ],
            }
        )
    )
    assert main(["scorecard", str(d), "--schema", str(g), "--json", "--suppressions", str(s)]) == 0
    doc = json.loads(capsys.readouterr().out)
    # the only consistency check is hidden, so the dimension has nothing left to score
    assert doc["dimensions"]["consistency"]["score"] is None
    assert doc["known_issues"][0]["reason"] == "orphans known"


def test_record_and_trend(data, tmp_path, capsys):
    d, g = data
    hist = tmp_path / "hist"
    base = [
        "scorecard",
        str(d),
        "--schema",
        str(g),
        "--json",
        "--history",
        str(hist),
        "--name",
        "orders",
    ]
    assert main(base + ["--record"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["trend"]["completeness"]["direction"] == "no data"
    assert main(base + ["--record"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["trend"]["completeness"]["direction"] == "steady"
    assert second["trend"]["completeness"]["previous"] == 93.75


def test_record_needs_history_and_name(data, capsys):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g), "--record"]) == 2
    assert "--history" in capsys.readouterr().err


def test_owners_from_project_dir(data, tmp_path, capsys, monkeypatch):
    d, g = data
    (tmp_path / "shape.yml").write_text("owners:\n  customer.name: data-team\n")
    assert (
        main(["scorecard", str(d), "--schema", str(g), "--json", "--project", str(tmp_path)]) == 0
    )
    doc = json.loads(capsys.readouterr().out)
    owners = {c["column"]: c["owner"] for c in doc["dimensions"]["completeness"]["checks"]}
    assert owners["name"] == "data-team" and owners["id"] is None


def test_missing_data_is_input_error(tmp_path, capsys):
    assert main(["scorecard", str(tmp_path / "nope")]) == 2


def test_negative_samples_is_input_error(data, capsys):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g), "--samples", "-1"]) == 2
