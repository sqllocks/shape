"""``shape fidelity --tier N`` and ``shape drift``."""

from __future__ import annotations

import json
import subprocess
import sys

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest
from fid_helpers import make_table

from shape.cli.main import main


@pytest.fixture
def dirs(tmp_path):
    real, synth = tmp_path / "real", tmp_path / "synth"
    for d, seed in ((real, 1), (synth, 2)):
        d.mkdir()
        pq.write_table(make_table(seed, n=400), d / "orders.parquet")
        pq.write_table(make_table(seed + 10, n=300, email_rate=0.95), d / "extras.parquet")
    return real, synth


def _run(argv, capsys):
    rc = main(argv)
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_tier_2_prints_json_per_table_and_exits_0_when_every_check_passes(dirs, capsys):
    rc, out, _ = _run(["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "2"], capsys)
    rep = json.loads(out)
    assert rc == 0 and rep["tier"] == 2 and rep["passed"] and set(rep["tables"]) == {"extras", "orders"}
    assert rep["tables"]["orders"]["format_preservation"]["email"]["passed"]


def test_tier_2_exits_1_below_the_pass_rate_and_text_format_prints_the_summary(tmp_path, capsys):
    real, synth = tmp_path / "r.csv", tmp_path / "s.csv"
    pacsv.write_csv(pa.table({"e": [f"a{i}@b.co" for i in range(40)]}), real)
    pacsv.write_csv(pa.table({"e": [f"a{i}" for i in range(40)]}), synth)
    rc, out, _ = _run(["fidelity", str(real), str(synth), "--tier", "2", "--format", "text"], capsys)
    assert rc == 1 and "Tier 2 fidelity report" in out and "[FAIL] e" in out
    rc, _, _ = _run(["fidelity", str(real), str(synth), "--tier", "2", "--min-pass-rate", "0"], capsys)
    assert rc == 0


def test_tier_2_anomaly_rate(tmp_path, capsys):
    flags = np.zeros(100, dtype=bool)
    flags[:30] = True
    a, b = tmp_path / "a.parquet", tmp_path / "b.parquet"
    pq.write_table(pa.table({"x": range(100)}), a)
    pq.write_table(pa.table({"x": range(100), "_shape_is_anomaly": flags}), b)
    assert _run(["fidelity", str(a), str(b), "--tier", "2"], capsys)[0] == 1
    rc, out, _ = _run(
        ["fidelity", str(a), str(b), "--tier", "2", "--expected-anomaly-rate", "0.3"], capsys
    )
    assert rc == 0 and json.loads(out)["tables"]["a"]["anomaly_rate"]["passed"]


def test_tier_1_reports_every_part_and_gates_on_the_auc(dirs, tmp_path, capsys):
    pytest.importorskip("sklearn")
    out_file = tmp_path / "t1.json"
    rc, out, err = _run(
        ["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "1", "-o", str(out_file)], capsys
    )
    rep = json.loads(out)
    t = rep["tables"]["orders"]
    assert rc == 0 and rep["passed"] and err == ""
    assert t["adversarial"]["auc_roc"] < 0.75 and t["gmm_fits"] and t["periodicity"]["wave"]
    assert json.loads(out_file.read_text()) == rep
    rc, _, _ = _run(["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "1", "--max-auc", "0.0"], capsys)
    assert rc == 1


def test_tier_1_without_scikit_learn_runs_and_warns_on_stderr(dirs, no_sklearn, capsys):
    rc, out, err = _run(["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "1"], capsys)
    rep = json.loads(out)
    assert rc == 0 and rep["tables"]["orders"]["adversarial"] is None
    assert "sqllocks-shape[advanced]" in err and any("skipped" in n for n in rep["notes"])


def test_tier_3_compares_the_trees_and_can_gate(dirs, capsys):
    rc, out, _ = _run(["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "3"], capsys)
    rep = json.loads(out)
    cmp = rep["tables"]["orders"]["comparison"]
    assert rc == 0 and 0 <= cmp["edge_overlap"] <= 1 and rep["tables"]["orders"]["reference"]["edges"]
    rc, _, _ = _run(
        ["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "3", "--min-edge-overlap", "1.01"], capsys
    )
    assert rc == 1


def test_a_missing_synthetic_table_fails_and_is_named(dirs, capsys):
    (dirs[1] / "extras.parquet").unlink()
    rc, out, err = _run(["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "2"], capsys)
    assert rc == 1 and "extras" in err and "extras" in json.loads(out)["notes"][0]


def test_tier_report_formats_and_bad_input(dirs, tmp_path, capsys):
    rc, _, err = _run(["fidelity", str(dirs[0]), str(dirs[1]), "--tier", "2", "--format", "md"], capsys)
    assert rc == 2 and "json or text" in err
    rc, _, err = _run(["fidelity", str(tmp_path / "nope"), str(dirs[1]), "--tier", "2"], capsys)
    assert rc == 2 and "error" in err


def test_the_base_report_is_untouched_without_tier(dirs, capsys):
    rc, out, _ = _run(["fidelity", str(dirs[0]), str(dirs[1])], capsys)
    assert "overall_score" in json.loads(out) and rc in (0, 1)


def test_drift_psi_exit_codes(dirs, tmp_path, capsys):
    rc, out, _ = _run(["drift", str(dirs[0]), str(dirs[1]), "--psi"], capsys)
    rep = json.loads(out)
    assert rc == 0 and not rep["drifted"] and rep["method"] == "psi"
    shifted = tmp_path / "shifted"
    shifted.mkdir()
    pq.write_table(make_table(5, n=400, shift=80.0), shifted / "orders.parquet")
    pq.write_table(make_table(6, n=300), shifted / "extras.parquet")
    out_file = tmp_path / "drift.json"
    rc, out, _ = _run(["drift", str(dirs[0]), str(shifted), "--psi", "-o", str(out_file)], capsys)
    rep = json.loads(out)
    assert rc == 1 and rep["drifted"] and "amount" in rep["tables"]["orders"]["drifted_columns"]
    assert json.loads(out_file.read_text()) == rep
    assert _run(["drift", str(dirs[0]), str(shifted), "--psi", "--threshold", "1e9"], capsys)[0] == 0


def test_drift_full_monitor_and_missing_scipy(dirs, tmp_path, capsys):
    pytest.importorskip("scipy")
    rc, out, _ = _run(["drift", str(dirs[0]), str(dirs[0])], capsys)
    assert rc == 0 and json.loads(out)["method"] == "ks+chi2+psi"
    rc, out, _ = _run(["drift", str(dirs[0]), str(dirs[1]), "--pvalue", "1.1"], capsys)
    assert rc == 1


def test_drift_without_scipy_names_the_extra(dirs, no_scipy, capsys):
    rc, _, err = _run(["drift", str(dirs[0]), str(dirs[1])], capsys)
    assert rc == 2 and "sqllocks-shape[advanced]" in err
    assert _run(["drift", str(dirs[0]), str(dirs[1]), "--psi"], capsys)[0] in (0, 1)


@pytest.mark.parametrize("argv", [["drift", "--help"], ["fidelity", "--help"]])
def test_start_up_stays_light(argv):
    code = (
        "import sys\n"
        "from shape.cli.main import main\n"
        f"try:\n    main({argv!r})\nexcept SystemExit:\n    pass\n"
        "heavy = [m for m in ('numpy', 'pyarrow', 'pandas', 'sklearn', 'scipy', 'shape.fidelity') "
        "if m in sys.modules]\n"
        "print('HEAVY', heavy)\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert "HEAVY []" in done.stdout, done.stdout[-500:] + done.stderr
    assert ("--psi" in done.stdout) or ("--tier" in done.stdout)
