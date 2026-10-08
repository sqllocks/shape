"""W1-17: chaos refuses input not marked as Shape-generated and never writes into it."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.chaos.input_check import ChaosInputError, check_output_folder, verify_chaos_input
from shape.cli.main import main
from shape.io.provenance import PROVENANCE_FILE

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))
from scale_schemas import plain_doc  # noqa: E402

ROWS = {"customer": 40, "order": 200, "order_line": 300}


def run(capsys: Any, *argv: Any) -> tuple[int, str, str]:
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def schema_file(tmp_path: Path) -> Path:
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc(ROWS)))
    return path


def generate(capsys: Any, schema: Path, folder: Path, fmt: str) -> None:
    code, out, err = run(capsys, "generate", schema, "-f", fmt, "-o", folder, "--seed", 3)
    assert code == 0, out + err


def log_header(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text().splitlines()[0])


@pytest.mark.parametrize("fmt", ["csv", "jsonl", "parquet"])
def test_generated_files_are_accepted_and_the_log_says_verified(
    capsys: Any, tmp_path: Path, schema_file: Path, fmt: str
) -> None:
    src, out = tmp_path / "gen", tmp_path / "chaos"
    generate(capsys, schema_file, src, fmt)
    code, _, err = run(
        capsys, "chaos", "--input", src, "-o", out, "--corrupt", "duplicates=0.05", "-f", fmt
    )
    assert code == 0, err
    assert "warning" not in err
    header = log_header(out / "_chaos_ground_truth.jsonl")
    assert header["input_provenance"] == "verified"


def test_chaos_writes_a_provenance_sidecar_in_its_output(
    capsys: Any, tmp_path: Path, schema_file: Path
) -> None:
    src, out = tmp_path / "gen", tmp_path / "chaos"
    generate(capsys, schema_file, src, "csv")
    code, _, err = run(capsys, "chaos", "--input", src, "-o", out, "--corrupt", "duplicates=0.05")
    assert code == 0, err
    doc = json.loads((out / PROVENANCE_FILE).read_text())
    assert doc["format"] == "shape-provenance" and doc["version"] == 1
    names = {e["path"] for e in doc["files"]}
    assert {"customer.csv", "order.csv", "order_line.csv"} <= names


def test_a_hand_written_csv_is_refused_before_anything_is_written(
    capsys: Any, tmp_path: Path
) -> None:
    src, out = tmp_path / "real", tmp_path / "chaos"
    src.mkdir()
    (src / "orders.csv").write_text("id,amount\n1,5\n2,6\n")
    code, _, err = run(capsys, "chaos", "--input", src, "-o", out, "--corrupt", "duplicates=0.5")
    assert code == 2
    assert (
        f"shape: error: chaos input {src / 'orders.csv'} is not marked as Shape-generated data; "
        "chaos only corrupts synthetic data (pass --allow-real-input to override)"
    ) in err
    assert "sha256 differs" not in err
    assert not out.exists()


def test_a_generated_file_edited_afterwards_is_refused(
    capsys: Any, tmp_path: Path, schema_file: Path
) -> None:
    src, out = tmp_path / "gen", tmp_path / "chaos"
    generate(capsys, schema_file, src, "csv")
    with (src / "customer.csv").open("a") as f:
        f.write("9999,x,1.0\n")
    code, _, err = run(capsys, "chaos", "--input", src, "-o", out, "--corrupt", "duplicates=0.05")
    assert code == 2
    assert f"chaos input {src / 'customer.csv'} is not marked as Shape-generated data" in err
    assert err.rstrip().endswith("(its sha256 differs from the provenance record)")
    assert not out.exists()


def test_one_real_file_among_generated_ones_refuses_the_folder(
    capsys: Any, tmp_path: Path, schema_file: Path
) -> None:
    src, out = tmp_path / "gen", tmp_path / "chaos"
    generate(capsys, schema_file, src, "csv")
    (src / "extra.csv").write_text("a\n1\n")
    code, _, err = run(capsys, "chaos", "--input", src, "-o", out, "--corrupt", "duplicates=0.05")
    assert code == 2 and "extra.csv is not marked" in err and not out.exists()


def test_allow_real_input_overrides_with_a_warning_and_logs_unverified(
    capsys: Any, tmp_path: Path
) -> None:
    src, out = tmp_path / "real", tmp_path / "chaos"
    src.mkdir()
    (src / "orders.csv").write_text("id,amount\n1,5\n2,6\n3,7\n4,8\n")
    code, _, err = run(
        capsys,
        "chaos",
        "--input",
        src,
        "-o",
        out,
        "--corrupt",
        "duplicates=0.5",
        "--allow-real-input",
    )
    assert code == 0, err
    assert (
        "shape: warning: --allow-real-input: corrupting data that is not marked as "
        "Shape-generated" in err
    )
    assert log_header(out / "_chaos_ground_truth.jsonl")["input_provenance"] == "unverified"


def test_allow_real_input_on_verified_input_does_not_warn(
    capsys: Any, tmp_path: Path, schema_file: Path
) -> None:
    src, out = tmp_path / "gen", tmp_path / "chaos"
    generate(capsys, schema_file, src, "csv")
    code, _, err = run(
        capsys,
        "chaos",
        "--input",
        src,
        "-o",
        out,
        "--corrupt",
        "duplicates=0.05",
        "--allow-real-input",
    )
    assert code == 0 and "warning" not in err
    assert log_header(out / "_chaos_ground_truth.jsonl")["input_provenance"] == "verified"


@pytest.mark.parametrize("where", ["same", "inside", "same_with_dots"])
def test_output_may_not_be_the_input_folder_or_inside_it(
    capsys: Any, tmp_path: Path, schema_file: Path, where: str
) -> None:
    src = tmp_path / "gen"
    generate(capsys, schema_file, src, "csv")
    before = {p.name: p.read_bytes() for p in src.iterdir()}
    target = {"same": src, "inside": src / "nested", "same_with_dots": src / "x" / ".."}[where]
    code, _, err = run(capsys, "chaos", "--input", src, "-o", target, "--corrupt", "duplicates=0.5")
    assert code == 2
    assert f"shape: error: chaos output {target} is the input folder; give a different -o" in err
    assert {p.name: p.read_bytes() for p in src.iterdir()} == before  # nothing was written
    assert not (src / "nested").exists()


def test_a_sibling_output_folder_is_fine(tmp_path: Path) -> None:
    check_output_folder(tmp_path / "gen", tmp_path / "gen_chaos")  # a longer name is not inside
    check_output_folder(tmp_path / "gen", tmp_path)  # the parent does not overwrite the input


def test_chaos_without_input_is_unaffected(capsys: Any, tmp_path: Path, schema_file: Path) -> None:
    out = tmp_path / "chaos"
    code, _, err = run(
        capsys, "chaos", schema_file, "-o", out, "--corrupt", "duplicates=0.05", "--seed", 1
    )
    assert code == 0, err
    assert "input_provenance" not in log_header(out / "_chaos_ground_truth.jsonl")
    assert (out / PROVENANCE_FILE).is_file()


def test_a_marked_parquet_file_needs_no_sidecar(tmp_path: Path) -> None:
    table = pa.table({"x": [1, 2, 3]}).replace_schema_metadata({"shape_synthetic": "true"})
    pq.write_table(table, tmp_path / "t.parquet")
    assert verify_chaos_input(tmp_path) == "verified"
    pq.write_table(pa.table({"x": [1]}), tmp_path / "u.parquet")  # an unmarked one beside it
    with pytest.raises(ChaosInputError, match="u.parquet is not marked"):
        verify_chaos_input(tmp_path)
    assert verify_chaos_input(tmp_path, allow_real_input=True) == "unverified"


def test_the_sidecar_and_success_are_not_tables(
    capsys: Any, tmp_path: Path, schema_file: Path
) -> None:
    from shape.cli.incremental import read_tables

    src = tmp_path / "gen"
    generate(capsys, schema_file, src, "csv")
    (src / "_SUCCESS").write_text("")
    assert set(read_tables(src)) == {"customer", "order", "order_line"}
    assert verify_chaos_input(src) == "verified"


def test_a_sidecar_from_a_newer_shape_is_an_input_error(
    capsys: Any, tmp_path: Path, schema_file: Path
) -> None:
    src, out = tmp_path / "gen", tmp_path / "chaos"
    generate(capsys, schema_file, src, "csv")
    side = src / PROVENANCE_FILE
    doc = json.loads(side.read_text())
    doc["version"] = 2
    side.write_text(json.dumps(doc))
    code, _, err = run(capsys, "chaos", "--input", src, "-o", out, "--corrupt", "duplicates=0.05")
    assert code == 2 and "newer Shape" in err and not out.exists()
