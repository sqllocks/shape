"""Driver logic of the timing scripts under ``benchmarks/`` that needs no baseline checkout: the
fresh-process workers are replaced by stand-ins, so only the scripts' own bookkeeping runs."""

from __future__ import annotations

import importlib.util
import math
import os
import sys
import types
from pathlib import Path

import pyarrow as pa
import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_refengine"
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
    mod = _load("vs_refengine_stream_prof_bench", BENCH / "stream_prof" / "bench.py")
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
    mod = _load("vs_refengine_kernel_bench", BENCH / "kernel_bench.py")
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
            case["native"] = lambda: [-1.0, *pa.array(native()).to_pylist()[1:]]
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


def test_live_fidelity_overhead_is_timed_under_the_benchmark_lock(monkeypatch, tmp_path):
    """Section 1.4: the tee's overhead is a timed benchmark, so it holds the exclusive lock and
    waits for the load gate before timing."""
    mod = _load("benchmarks_live_fidelity_run", ROOT / "benchmarks" / "live_fidelity" / "run.py")
    import common

    monkeypatch.setattr(common, "BENCH_OUT_DIR", tmp_path)
    monkeypatch.delenv("BENCH_LOCK_HELD", raising=False)
    gated = []
    monkeypatch.setattr(
        mod, "wait_for_quiet", lambda *a, **k: gated.append(1) or 0.0, raising=False
    )
    monkeypatch.setattr(mod, "load_schema", lambda name, work: object())
    monkeypatch.setattr(mod, "generate_tables", lambda schema, scale, seed: {})
    held = []

    def rate(schema, scale, reference, mode, sink_kind, work):
        held.append(__import__("os").environ.get("BENCH_LOCK_HELD"))
        return 100.0

    monkeypatch.setattr(mod, "rate", rate)
    mod.overhead("retail", "small", tmp_path, 1)
    assert held and set(held) == {"1"}
    assert gated


@pytest.mark.parametrize(
    ("platform", "maxrss", "mb"), [("linux", 1_048_576, 1024.0), ("darwin", 1 << 30, 1024.0)]
)
def test_peak_rss_is_megabytes_on_every_platform(monkeypatch, platform, maxrss, mb):
    """``ru_maxrss`` is KiB on Linux and bytes on macOS; the harness records megabytes."""
    gen = _load("vs_refengine_domain_generate", BENCH / "domain_1to1" / "generate.py")
    import common

    class Usage:
        ru_maxrss = maxrss
        ru_utime, ru_stime, ru_minflt, ru_majflt = 1.5, 0.5, 7, 1

    fake = types.SimpleNamespace(RUSAGE_SELF=0, RUSAGE_CHILDREN=-1, getrusage=lambda who: Usage())
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(common, "resource", fake)  # the real module does not exist on Windows
    assert gen._peak_rss_mb() == mb
    assert gen._ru() == {"user": 1.5, "sys": 0.5, "minflt": 7, "majflt": 1}


def test_peak_rss_and_cpu_time_are_measured_on_windows_without_resource(monkeypatch):
    """Windows has no ``resource`` module: the peak working set (bytes) and the page faults come
    from GetProcessMemoryInfo, the CPU time from ``os.times()``; children are not measured."""
    gen = _load("vs_refengine_domain_generate", BENCH / "domain_1to1" / "generate.py")
    import common

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(common, "resource", None)
    monkeypatch.setattr(common, "windows_memory", lambda: (1 << 30, 42))
    assert gen._peak_rss_mb() == 1024.0
    ru = gen._ru()
    assert ru["minflt"] == 42 and ru["majflt"] == 0
    assert ru["user"] >= 0 and ru["sys"] >= 0
    assert all(math.isnan(v) for v in common.rusage(children=True))


def test_the_scripts_load_where_there_is_no_resource_module(monkeypatch):
    """Importing ``resource`` fails on Windows; no harness script may need it to load."""
    monkeypatch.setitem(sys.modules, "resource", None)  # import raises ImportError, as on Windows
    assert _load("vs_refengine_common_no_resource", BENCH / "common.py").resource is None
    for name, path in [
        ("vs_refengine_domain_generate_no_resource", BENCH / "domain_1to1" / "generate.py"),
        ("vs_refengine_domain_bench_cli_no_resource", BENCH / "domain_1to1" / "bench_cli.py"),
        ("vs_refengine_learn_bench_cli_no_resource", BENCH / "learn_1to1" / "bench_cli.py"),
    ]:
        for sibling in ("compare", "composites"):  # each folder has its own: not another's
            monkeypatch.delitem(sys.modules, sibling, raising=False)
        _load(name, path)
    for path in BENCH.rglob("*.py"):
        if path.name != "common.py":
            assert "import resource" not in path.read_text(encoding="utf-8"), path


def test_the_benchmark_lock_is_held_on_windows_too(monkeypatch, tmp_path):
    """Section 1.4 on Windows: ``msvcrt.locking`` takes the lock (and keeps waiting while another
    run holds it), ``BENCH_LOCK_HELD`` is exported while it is held, and it is released after."""
    import common

    calls = []
    busy = [True]

    def locking(fd, mode, nbytes):
        calls.append((mode, nbytes, os.environ.get("BENCH_LOCK_HELD")))
        if mode == fake.LK_LOCK and busy:
            busy.pop()
            raise OSError(36, "Resource deadlock avoided")  # LK_LOCK gave up after ~10 s

    fake = types.SimpleNamespace(LK_LOCK=1, LK_UNLCK=0, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(common, "WINDOWS", True)
    monkeypatch.setattr(common, "BENCH_OUT_DIR", tmp_path)
    monkeypatch.delenv("BENCH_LOCK_HELD", raising=False)
    with common.bench_lock():
        assert os.environ["BENCH_LOCK_HELD"] == "1"
        with common.bench_lock():  # re-entrant: no second lock
            pass
    assert calls == [(1, 1, None), (1, 1, None), (0, 1, None)]  # retried, then released
    assert "BENCH_LOCK_HELD" not in os.environ
    assert (tmp_path / "bench.lock").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["D5"], "invalid choice"),
        (["D3", "--rows", "-5"], "positive"),
        (["D3", "--rows", "0"], "positive"),
    ],
)
def test_datasets_rejects_unknown_names_and_bad_row_counts(tmp_path, monkeypatch, args, message):
    """An unknown dataset or a row count below 1 is a usage error (exit 2) that says what is
    accepted; nothing is written."""
    import subprocess

    monkeypatch.setenv("PROFILE_DATA_DIR", str(tmp_path))
    script = BENCH / "profile_1to1" / "datasets.py"
    r = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
    assert r.returncode == 2, r.stderr
    assert message in r.stderr and "Traceback" not in r.stderr
    assert list(tmp_path.iterdir()) == []


def test_datasets_without_names_means_every_dataset():
    datasets = _load("vs_refengine_profile_datasets", BENCH / "profile_1to1" / "datasets.py")
    assert datasets.parse_args([]).datasets == list(datasets.ALL)
    assert datasets.parse_args(["d2", "mt"]).datasets == ["D2", "MT"]
