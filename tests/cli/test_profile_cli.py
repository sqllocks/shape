"""The profiling CLI (P1-11): version, lazy imports, capture, diff, inspect, spindle-compat."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import shape
from shape.cli.main import main


def cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *args], capture_output=True, text=True, check=False
    )


@pytest.fixture()
def orders_csv(tmp_path: Path) -> Path:
    rows = ["id,status,region,amount,note"]
    for i in range(200):
        status = ["new", "paid", "paid", "shipped"][i % 4]
        region = ["eu", "us"][i % 2]
        rows.append(f"{i},{status},{region},{i * 1.5},note {i}")
    path = tmp_path / "orders.csv"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_version_flag_prints_and_loads_nothing_heavy():
    code = (
        "import sys\n"
        "from shape.cli.main import main\n"
        "rc = main(['--version'])\n"
        "heavy = [m for m in ('numpy', 'pyarrow', 'pandas', 'shape.profile', 'shape.api') "
        "if m in sys.modules]\n"
        "print('HEAVY', heavy, rc)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.splitlines()[0] == f"shape {shape.__version__}"
    assert done.stdout.splitlines()[1] == "HEAVY [] 0"


def test_import_shape_is_lazy_and_the_public_names_still_work():
    code = (
        "import sys, shape\n"
        "assert 'numpy' not in sys.modules\n"
        "assert callable(shape.profile) and callable(shape.query) and callable(shape.diff)\n"
        "import shape.profile.engine\n"
        "assert callable(shape.profile)  # the package is callable once imported\n"
        "print('ok', shape.__version__)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == f"ok {shape.__version__}"


def test_capture_records_categorical_shares_and_no_rows(tmp_path, orders_csv, capsys):
    out = tmp_path / "cap" / "orders.profile.json"
    assert main(["profile", "capture", str(orders_csv), "-o", str(out)]) == 0
    cap = json.loads(out.read_text(encoding="utf-8"))
    assert cap["name"] == "captured" and cap["source_domain"] == "captured" and cap["ratios"] == {}
    assert cap["description"] == f"Captured shape from {orders_csv}"
    assert cap["metadata"]["tables"]["orders"] == {
        "rows": 200,
        "columns": ["id", "status", "region", "amount", "note"],
    }
    dist = cap["distributions"]
    assert dist["orders.status"] == {"paid": 0.5, "new": 0.25, "shipped": 0.25}
    assert dist["orders.region"] == {"eu": 0.5, "us": 0.5}
    # numbers are not categorical; 200 distinct notes are more than 50 and not under 5%
    assert set(dist) == {"orders.status", "orders.region"}
    assert "note 7" not in out.read_text(encoding="utf-8")
    assert "200 rows" in capsys.readouterr().out.replace(",", "")


def test_capture_of_a_directory_takes_every_file_sorted(tmp_path):
    d = tmp_path / "data"
    d.mkdir()
    (d / "b.csv").write_text("k,v\n" + "\n".join(f"x,{i}" for i in range(4)) + "\n")
    (d / "a.csv").write_text("g\n" + "\n".join("u" for _ in range(3)) + "\n")
    out = tmp_path / "o.json"
    assert main(["profile", "capture", str(d), "-o", str(out), "--name", "demo"]) == 0
    cap = json.loads(out.read_text())
    assert cap["name"] == "demo"
    assert list(cap["metadata"]["tables"]) == ["a", "b"]
    assert list(cap["distributions"]) == ["a.g", "b.k"]
    empty = tmp_path / "empty"
    empty.mkdir()
    assert main(["profile", "capture", str(empty), "-o", str(tmp_path / "e.json")]) == 1
    assert main(["profile", "capture", str(tmp_path / "missing"), "-o", str(out)]) == 2


def test_capture_handles_categoricals_objects_and_nulls(tmp_path):
    import decimal

    import pyarrow as pa
    import pyarrow.parquet as pq

    table = pa.table(
        {
            "cat": pa.array(["a", "b", "a", None, "a"]).dictionary_encode(),
            "dec": pa.array([decimal.Decimal("1.50")] * 3 + [decimal.Decimal("2.00")] * 2),
            "flag": pa.array([True, None, False, True, True]),
            "n": pa.array([1, 2, 3, 4, 5]),
        }
    )
    src = tmp_path / "t.parquet"
    pq.write_table(table, src)
    out = tmp_path / "o.json"
    assert main(["profile", "capture", str(src), "--format", "parquet", "-o", str(out)]) == 0
    dist = json.loads(out.read_text())["distributions"]
    assert dist["t.cat"] == {"a": 0.75, "b": 0.25}
    assert dist["t.dec"] == {"1.50": 0.6, "2.00": 0.4}
    assert dist["t.flag"] == {"True": 0.75, "False": 0.25}  # bool with nulls is an object column
    assert "t.n" not in dist


def test_profile_diff_is_total_variation_distance(tmp_path, capsys):
    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"distributions": {"t.s": {"x": 0.5, "y": 0.5}, "t.gone": {"q": 1.0}}}))
    b.write_text(
        json.dumps(
            {"distributions": {"t.s": {"x": 0.25, "y": 0.5, "z": 0.25}, "t.new": {"r": 1.0}}}
        )
    )
    assert main(["profile", "diff", str(a), str(b), "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["added"] == ["t.new"] and rep["removed"] == ["t.gone"]
    assert rep["keys"]["t.s"]["tvd"] == 0.25 and rep["total_drift"] == 0.25
    assert rep["keys"]["t.s"]["changes"] == {"x": [0.5, 0.25], "z": [0.0, 0.25]}
    assert main(["profile", "diff", str(a), str(b), "--threshold", "0.2"]) == 1
    assert main(["profile", "diff", str(a), str(b), "--threshold", "0.3"]) == 0
    assert main(["profile", "diff", str(a), str(tmp_path / "nope.json")]) == 2
    text = capsys.readouterr().out
    assert "Total shape drift: 0.250" in text and "x" in text


def test_spindle_compat_bare_writes_the_spindle_json_to_the_output(tmp_path, orders_csv, capsys):
    out = tmp_path / "spindle.json"
    assert main(["profile", str(orders_csv), "--spindle-compat", "-o", str(out)]) == 0
    doc = json.loads(out.read_text())
    assert doc["row_count"] == 200 and set(doc["columns"]) == {
        "id",
        "status",
        "region",
        "amount",
        "note",
    }
    assert doc == shape.profile(str(orders_csv)).to_dict()
    assert json.loads(capsys.readouterr().out)["format"] == "spindle-compat"
    assert main(["profile", str(orders_csv), "--spindle-compat"]) == 2  # needs -o
    assert main(["profile", str(orders_csv)]) == 2  # a .shape output is needed


def test_spindle_compat_with_a_path_keeps_the_shape_file(tmp_path, orders_csv):
    out, full = tmp_path / "p.shape", tmp_path / "full.json"
    assert main(["profile", str(orders_csv), "-o", str(out), "--spindle-compat", str(full)]) == 0
    assert out.exists() and json.loads(full.read_text())["row_count"] == 200


def test_inspect_and_show_read_profiles_and_models(tmp_path, orders_csv, capsys):
    prof = tmp_path / "p.shape"
    assert main(["profile", str(orders_csv), "-o", str(prof)]) == 0
    capsys.readouterr()
    for name in ("inspect", "show"):
        assert main([name, str(prof)]) == 0
        out = json.loads(capsys.readouterr().out)
        assert out["kind"] == "profile" and out["profile"]["row_count"] == 200
    from shape.artifact import write_model

    model = tmp_path / "m.shape"
    write_model(model, {"rows": 3, "columns": {"a": {"kind": "numeric", "count": 3}}}, name="m")
    assert main(["inspect", str(model)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["kind"] == "model" and out["shape"]["tables"]["m"]["rows"] == 3
    assert main(["inspect", str(tmp_path / "missing.shape")]) == 2


def test_version_command_reports_the_v2_format(capsys):
    assert main(["version"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["artifact_format"] == 2 and out["shape"] == shape.__version__


def test_check_exits_nonzero_on_a_failed_contract(tmp_path, orders_csv):
    prof = tmp_path / "p.shape"
    assert main(["profile", str(orders_csv), "-o", str(prof)]) == 0
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps({"row_count": {"min": 10}}))
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"row_count": {"min": 10**6}}))
    assert main(["check", str(prof), str(ok)]) == 0
    assert main(["check", str(prof), str(bad)]) == 1


def test_validate_accepts_a_contract_document(capsys):
    assert main(["validate", "examples/customer.shape.json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True
