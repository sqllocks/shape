"""Driver logic of the timing scripts under ``benchmarks/`` that needs no baseline checkout: the
fresh-process workers are replaced by stand-ins, so only the scripts' own bookkeeping runs."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def stream_prof(monkeypatch, tmp_path):
    mod = _load("vs_spindle_stream_prof_bench", BENCH / "stream_prof" / "bench.py")
    monkeypatch.setattr(mod, "PROFILE_DATA_DIR", tmp_path)
    (tmp_path / "d2.parquet").write_bytes(b"")
    monkeypatch.setattr(mod, "wait_for_quiet", lambda *a, **k: 0.0)
    monkeypatch.setenv("BENCH_LOCK_HELD", "1")
    return mod


def test_stream_prof_refuses_a_ratio_when_the_stream_side_saw_fewer_rows(
    stream_prof, monkeypatch, tmp_path, capsys
):
    """The STREAM-PROF figure is rows/s of the replay over rows/s of the batch. A replay that
    profiled fewer rows than the batch side must not be reported as a throughput ratio."""
    rows = {"batch": 1_000_000, "stream": 500_000}
    monkeypatch.setattr(
        stream_prof,
        "run_once",
        lambda side, path, batch, threads: {"seconds": 1.0, "rows": rows[side]},
    )
    out = tmp_path / "r.json"
    rc = stream_prof.main(["--skip-verify", "--reps", "1", "--out", str(out)])
    assert rc == 1
    assert "rows" in capsys.readouterr().err


@pytest.fixture
def measure(monkeypatch, tmp_path):
    mod = _load("benchmarks_measure_product", ROOT / "benchmarks" / "measure_product.py")
    monkeypatch.setattr(mod, "OUT", tmp_path / "product_bench.json")
    monkeypatch.setattr(mod, "DATASETS", [("d1.parquet", 200_000, 6)])
    monkeypatch.setattr(mod, "_timed", lambda cmd: 0.1)
    monkeypatch.setattr(mod, "wait_for_quiet", lambda *a, **k: 0.0, raising=False)
    monkeypatch.setenv("BENCH_LOCK_HELD", "1")
    return mod


def test_measure_product_refuses_output_that_differs_between_runs(measure, monkeypatch):
    """Numbers are recorded only for output that is the same on every run: a run whose profile
    differs must stop the script and leave the evidence file unwritten."""
    shas = iter(["a", "b", "a"])
    monkeypatch.setattr(
        measure,
        "_child",
        lambda path: {"s": 1.0, "peak_rss_mb": 1.0, "rows": 200_000, "sha256": next(shas)},
    )
    with pytest.raises(SystemExit, match="differs between runs"):
        measure.main()
    assert not measure.OUT.exists()


def test_measure_product_names_a_dataset_with_the_wrong_row_count(measure, monkeypatch):
    monkeypatch.setattr(
        measure,
        "_child",
        lambda path: {"s": 1.0, "peak_rss_mb": 1.0, "rows": 5, "sha256": "a"},
    )
    with pytest.raises(SystemExit, match=r"d1\.parquet.*5 rows.*200000"):
        measure.main()
    assert not measure.OUT.exists()


@pytest.fixture
def kernel_bench(monkeypatch):
    pytest.importorskip("shape._kernel")
    mod = _load("vs_spindle_kernel_bench", BENCH / "kernel_bench.py")
    monkeypatch.setattr(mod, "wait_for_quiet", lambda *a, **k: 0.0)
    monkeypatch.setenv("BENCH_LOCK_HELD", "1")
    return mod


def test_kernel_bench_checks_the_timed_output_itself(kernel_bench, monkeypatch, tmp_path):
    """Equivalence is checked on the output being timed: a kernel whose full-size result is
    wrong (while a separate small call is right) must not be recorded."""
    real = kernel_bench.cases

    def cases(n):
        out = real(n)
        if n == 2000:  # the timed size: corrupt the first value of one kernel's result
            case = next(c for c in out if c["name"] == "philox_uniform")
            native = case["native"]
            case["native"] = lambda: [-1.0, *list(native())[1:]]
        return out

    monkeypatch.setattr(kernel_bench, "cases", cases)
    out = tmp_path / "results.json"
    out.write_text("{}")
    rc = kernel_bench.main(
        ["--rows", "2000", "--ref-rows", "500", "--runs", "1", "--out", str(out)]
    )
    assert rc == 1
    assert "kernel_microbench" not in out.read_text()


def test_kernel_bench_reports_a_missing_results_file_before_timing(kernel_bench, tmp_path, capsys):
    rc = kernel_bench.main(["--rows", "100", "--runs", "1", "--out", str(tmp_path / "none.json")])
    assert rc == 2
    assert "none.json" in capsys.readouterr().err
