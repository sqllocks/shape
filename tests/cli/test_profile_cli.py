"""The profiling CLI (P1-11): version, lazy imports, capture, diff, inspect, refengine-compat."""

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


def test_profile_needs_an_output_and_has_no_capture_or_diff_subcommand(tmp_path, orders_csv):
    assert main(["profile", str(orders_csv)]) == 2  # a .shape output is needed
    with pytest.raises(SystemExit):
        main(["profile", str(orders_csv), "-o", str(tmp_path / "p.shape"), "--full-json"])


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
