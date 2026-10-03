"""``shape scorecard --slice-by`` and the scorecard JSON Schemas (W3-11, items 1 to 3)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.schemacheck import validate

SCHEMA = {
    "format": "shape-gates",
    "version": 1,
    "tables": {
        "customer": {
            "primary_key": ["id"],
            "columns": {
                "id": {"type": "integer"},
                "name": {"type": "string", "nullable": False},
                "region": {"type": "string", "nullable": True},
                "churned": {"type": "boolean", "nullable": True},
            },
        }
    },
}


def region_table(name_nulls_south=10, churn=(9, 18)):
    n = 40
    ids = list(range(1, 3 * n + 1))
    names = [f"n{i}" for i in ids]
    for i in range(n, n + name_nulls_south):
        names[i] = None
    churned = (
        [True] * 9
        + [False] * 31
        + [True] * churn[1]
        + [False] * (n - churn[1])
        + [True] * 20
        + [False] * 20
    )
    return pa.table(
        {
            "id": ids,
            "name": names,
            "region": ["north"] * n + ["south"] * n + ["west"] * n,
            "churned": churned,
        }
    )


@pytest.fixture
def data(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    pq.write_table(region_table(), d / "customer.parquet")
    g = tmp_path / "gates.json"
    g.write_text(json.dumps(SCHEMA))
    return d, g


def run_json(capsys, *argv):
    code = main(["scorecard", *map(str, argv), "--json"])
    out = capsys.readouterr()
    return code, (json.loads(out.out) if out.out.strip() else None), out.err


def test_slice_by_scores_each_slice(data, capsys):
    d, g = data
    code, doc, _ = run_json(capsys, d, "--schema", g, "--slice-by", "region")
    assert code == 0  # without --max-slice-gap the scorecard exits 0 as before
    assert doc["version"] == 2
    comp = doc["slices"]["tables"]["customer"]["dimensions"]["completeness"]
    assert comp["worst_slice"] == "south"
    assert comp["gap"] == 12.5


def test_without_slice_by_it_is_version_1(data, capsys):
    d, g = data
    code, doc, _ = run_json(capsys, d, "--schema", g)
    assert code == 0 and doc["version"] == 1 and "slices" not in doc


def test_max_slice_gap_exit_codes(data, capsys):
    d, g = data
    assert (
        run_json(capsys, d, "--schema", g, "--slice-by", "region", "--max-slice-gap", "12")[0] == 1
    )
    code, doc, _ = run_json(
        capsys, d, "--schema", g, "--slice-by", "region", "--max-slice-gap", "12"
    )
    assert doc["slices"]["exceeded"] == [
        {"table": "customer", "dimension": "completeness", "gap": 12.5}
    ]
    # a gap equal to G does not exceed it
    assert (
        run_json(capsys, d, "--schema", g, "--slice-by", "region", "--max-slice-gap", "12.5")[0]
        == 0
    )
    assert (
        run_json(capsys, d, "--schema", g, "--slice-by", "region", "--max-slice-gap", "50")[0] == 0
    )


def test_max_slice_gap_in_markdown(data, capsys):
    d, g = data
    assert (
        main(
            [
                "scorecard",
                str(d),
                "--schema",
                str(g),
                "--slice-by",
                "region",
                "--max-slice-gap",
                "1",
            ]
        )
        == 1
    )
    out = capsys.readouterr().out
    assert "## Slices" in out and "Gaps above 1:" in out and "customer.completeness: 12.5" in out


def test_min_slice_rows(data, capsys):
    d, g = data
    code, doc, _ = run_json(
        capsys, d, "--schema", g, "--slice-by", "region", "--min-slice-rows", "41"
    )
    assert code == 0
    t = doc["slices"]["tables"]["customer"]
    assert t["slices"] == [] or [s["slice"] for s in t["slices"]] == ["(small slices)"]
    assert "north" not in json.dumps(doc["slices"])


def test_two_slice_columns(tmp_path, capsys):
    t = region_table().append_column("tier", pa.array(["a", "b"] * 60))
    d = tmp_path / "d"
    d.mkdir()
    pq.write_table(t, d / "customer.parquet")
    code, doc, _ = run_json(capsys, d, "--slice-by", "region,tier", "--min-slice-rows", "10")
    assert code == 0
    labels = [s["slice"] for s in doc["slices"]["tables"]["customer"]["slices"]]
    assert "region=north, tier=a" in labels


def test_label_and_reference_options(data, tmp_path, capsys):
    d, g = data
    ref = tmp_path / "ref.csv"
    pacsv.write_csv(pa.table({"region": ["north"] * 50 + ["south"] * 30 + ["west"] * 20}), ref)
    code, doc, _ = run_json(
        capsys, d, "--schema", g, "--slice-by", "region", "--label", "churned", "--reference", ref
    )
    assert code == 0
    t = doc["slices"]["tables"]["customer"]
    by = {s["slice"]: s for s in t["slices"]}
    assert by["north"]["reference_share"] == 0.5
    assert by["north"]["ratio"] == pytest.approx(0.6667, abs=1e-4)
    assert t["label"]["rates"] == {"north": 0.225, "south": 0.45, "west": 0.5}
    assert t["label"]["disparity_ratio"] == 0.45
    assert t["label"]["flagged"] is True


def test_reference_profile_file(data, tmp_path, capsys):
    d, g = data
    ref = tmp_path / "pop.json"
    ref.write_text(
        json.dumps(
            {
                "tables": {
                    "customer": {
                        "rows": 100,
                        "columns": [
                            {
                                "name": "region",
                                "kind": "text",
                                "count": 100,
                                "null_count": 0,
                                "distinct": 3,
                                "top": [["north", 50], ["south", 30], ["west", 20]],
                            }
                        ],
                    }
                }
            }
        )
    )
    code, doc, _ = run_json(capsys, d, "--schema", g, "--slice-by", "region", "--reference", ref)
    assert code == 0
    by = {s["slice"]: s for s in doc["slices"]["tables"]["customer"]["slices"]}
    assert by["south"]["reference_share"] == 0.3


@pytest.mark.parametrize(
    "extra",
    [
        ["--reference", "x"],
        ["--label", "churned"],
        ["--max-slice-gap", "1"],
    ],
)
def test_slice_options_need_slice_by(data, capsys, extra):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g), *map(str, extra)]) == 2
    assert "--slice-by" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--slice-by", "nope"], "no table holds"),
        (["--slice-by", "region,"], "COLUMN"),
        (["--slice-by", "region", "--min-slice-rows", "0"], "min_slice_rows"),
        (["--slice-by", "region", "--max-slice-gap", "-1"], "max_slice_gap"),
        (["--slice-by", "region", "--label", "name"], "two values"),
        (["--slice-by", "region", "--label", "zzz"], "not a column"),
        (["--slice-by", "region", "--reference", "missing.csv"], "not found"),
    ],
)
def test_bad_slice_input_is_exit_2(data, capsys, argv, message):
    d, g = data
    assert main(["scorecard", str(d), "--schema", str(g), *argv]) == 2
    assert message in capsys.readouterr().err


def test_classified_slice_column_is_numbered_unless_shown(data, capsys):
    d, g = data
    _, doc, _ = run_json(
        capsys, d, "--schema", g, "--slice-by", "region", "--classified", "customer.region"
    )
    labels = [s["slice"] for s in doc["slices"]["tables"]["customer"]["slices"]]
    assert labels == ["slice 1", "slice 2", "slice 3"]
    _, doc, _ = run_json(
        capsys,
        d,
        "--schema",
        g,
        "--slice-by",
        "region",
        "--classified",
        "customer.region",
        "--show-classified",
    )
    assert "north" in json.dumps(doc["slices"])


def test_history_and_record_compare_slice_gaps(data, tmp_path, capsys):
    d, g = data
    reg = tmp_path / "reg"
    base = ["--schema", g, "--slice-by", "region", "--history", reg, "--name", "cust"]
    assert main(["scorecard", str(d), *map(str, base), "--record", "--json"]) == 0
    capsys.readouterr()
    pq.write_table(region_table(name_nulls_south=20), d / "customer.parquet")
    code, doc, _ = run_json(capsys, d, *base)
    assert code == 0
    assert doc["slices"]["trend"]["customer.completeness"] == {
        "previous": 12.5,
        "change": 12.5,
        "direction": "widening",
    }


# -- JSON Schemas for versions 1 and 2 ---------------------------------------------------------


def schema(version):
    text = (
        resources.files("shape")
        .joinpath(f"schemas/scorecard-v{version}.schema.json")
        .read_text("utf-8")
    )
    return json.loads(text)


def test_version_1_output_validates_against_the_v1_schema(data, capsys):
    d, g = data
    _, doc, _ = run_json(capsys, d, "--schema", g)
    assert validate(doc, schema(1)) == []
    assert validate({**doc, "version": 2}, schema(1)) != []


def test_version_2_output_validates_against_the_v2_schema(data, tmp_path, capsys):
    d, g = data
    ref = tmp_path / "ref.csv"
    pacsv.write_csv(pa.table({"region": ["north"] * 50 + ["south"] * 30 + ["east"] * 20}), ref)
    _, doc, _ = run_json(
        capsys,
        d,
        "--schema",
        g,
        "--slice-by",
        "region",
        "--label",
        "churned",
        "--reference",
        ref,
        "--max-slice-gap",
        "5",
        "--history",
        tmp_path / "r",
        "--name",
        "x",
    )
    assert doc["slices"]["tables"]["customer"]["missing_from_data"]
    assert validate(doc, schema(2)) == []
    assert validate({**doc, "version": 1}, schema(2)) != []
    broken = json.loads(json.dumps(doc))
    del broken["slices"]["by"]
    assert validate(broken, schema(2)) != []
    assert validate({k: v for k, v in doc.items() if k != "slices"}, schema(2)) != []


def test_schema_files_are_in_the_source_tree():
    root = Path(__file__).resolve().parents[2] / "src" / "shape" / "schemas"
    for v in (1, 2):
        doc = json.loads((root / f"scorecard-v{v}.schema.json").read_text())
        assert doc["properties"]["version"] == {"const": v}
        assert doc["properties"]["format"] == {"const": "shape-scorecard"}
