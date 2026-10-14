"""GitHub issue #1: ``shape profile`` failed on any nested column and a Delta table aborted.

* ``shape profile`` exits 0 on Parquet, Delta and JSON Lines inputs with list, struct, map and
  nested-in-nested columns, alone and mixed with flat columns, under both kernels.
* A local Delta read ends the process cleanly: deltalake's pyarrow dataset used to free its
  Python-backed file system on an Arrow pool thread after Python had shut down (the thread waited
  for the GIL, the main thread for the pool: a hang or ``terminate called without an active
  exception``). A table without a nested column did it too.
* No failure aborts: it is one ``shape: error:`` line and exit 2.
"""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import nested_inputs as ni
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.cli.main import main
from shape.kernel import dispatch


@pytest.fixture(params=["python", "rust"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    assert dispatch.kernel_name() == request.param
    yield request.param
    dispatch.reset()


def _names(kind: str) -> tuple[str, ...]:
    return ni.JSONL_NESTED if kind == "jsonl" else tuple(ni.NESTED_TYPES)


@pytest.mark.parametrize("kind", ["parquet", "delta", "jsonl"])
@pytest.mark.parametrize("shape_of", ["alone", "mixed", "all"])
def test_profile_exits_zero_on_nested_columns(
    kernel: str, kind: str, shape_of: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    names = _names(kind)
    cases = {
        "alone": [ni.nested_only(n) for n in names],
        "mixed": [ni.table_with(n) for n in names],
        "all": [ni.table_with(*names)],
    }[shape_of]
    for i, table in enumerate(cases):
        src = ni.write(kind, tmp_path, table, stem=f"t{i}")
        out = tmp_path / f"t{i}.shape"
        code = main(["profile", str(src), "-o", str(out)])
        err = capsys.readouterr().err
        assert code == 0, f"{kind} {table.schema.names}: {err}"
        assert "shape: error" not in err
        assert out.exists()


def test_the_report_repro_of_the_issue(tmp_path: Path) -> None:
    pq.write_table(pa.table({"id": [1, 2], "li": [[1], [2]]}), str(tmp_path / "a.parquet"))
    done = subprocess.run(
        [sys.executable, "-m", "shape.cli.main", "profile", "a.parquet", "-o", "a.shape"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert "unhashable" not in done.stderr
    assert shape.profile(str(tmp_path / "a.parquet")).to_dict()["columns"]["li"]["dtype"]


def _run_profile(src: Path, out: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", "profile", str(src), "-o", str(out)],
        capture_output=True,
        text=True,
        timeout=60,  # a hang at exit is a failure, not a wait
        check=False,
    )


@pytest.mark.parametrize("nested", [False, True], ids=["flat", "struct"])
def test_a_local_delta_table_ends_the_process_cleanly(tmp_path: Path, nested: bool) -> None:
    table = ni.table_with("st") if nested else ni.flat_table()
    src = ni.write_delta(tmp_path / "d", table)
    for run in range(8):  # the hang was a race: about two runs in three
        try:
            done = _run_profile(src, tmp_path / "d.shape")
        except subprocess.TimeoutExpired:  # pragma: no cover - the failure being tested
            pytest.fail(f"run {run}: the process did not end (hang at exit)")
        assert done.returncode == 0, f"run {run} exit {done.returncode}: {done.stderr[-300:]}"
        assert "terminate called" not in done.stderr


def test_a_broken_delta_log_is_one_error_line_and_exit_2(tmp_path: Path) -> None:
    src = ni.write_delta(tmp_path / "d", ni.flat_table())
    for log in (src / "_delta_log").glob("*.json"):
        log.write_text("not json\n")
    done = _run_profile(src, tmp_path / "d.shape")
    assert done.returncode == 2
    lines = [x for x in done.stderr.splitlines() if x.strip() and "RuntimeWarning" not in x]
    assert len(lines) == 1 and lines[0].startswith("shape: error:"), done.stderr
    assert "<unknown>" not in lines[0]  # delta-rs appends its backtrace to the message


def test_a_command_that_loaded_deltalake_ends_the_process_by_itself(tmp_path: Path) -> None:
    """A read through deltalake's Python file system (a table behind an object store) is marked
    (``deltafs.note_bridge``): the command line then ends the process once its output is flushed,
    so a scan still running on Arrow's pool threads cannot hold the interpreter's exit. The
    script leaves such a scan behind."""
    from deltalake import write_deltalake

    src = tmp_path / "d"
    for i in range(30):  # many files, so the scan is still going when the script ends
        part = pa.table(
            {"id": list(range(i * 1000, (i + 1) * 1000)), "s": [{"a": j} for j in range(1000)]}
        )
        write_deltalake(str(src), part, mode="append")
    script = (
        "import sys\n"
        "import importlib\n"
        "cli = importlib.import_module('shape.cli.main')\n"
        "from deltalake import DeltaTable\n"
        "from shape.io import deltafs\n"
        "def command(argv):\n"  # a command whose read goes through the Python file system
        "    deltafs.note_bridge()\n"
        f"    scan = DeltaTable({str(src)!r}).to_pyarrow_dataset().scanner(batch_size=2)\n"
        "    reader = scan.to_reader()\n"
        "    reader.read_next_batch()\n"
        "    return 0\n"
        "cli._main = command\n"
        "sys.argv = ['shape', 'version']\n"
        "cli.main()\n"
    )
    for run in range(3):
        try:
            done = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
            )
        except subprocess.TimeoutExpired:  # pragma: no cover - the failure being tested
            pytest.fail(f"run {run}: the process did not end")
        assert done.returncode == 0, f"run {run}: exit {done.returncode}: {done.stderr[-300:]}"


def test_a_flat_local_delta_table_profiles_cleanly_twenty_times(tmp_path: Path) -> None:
    """A guard: profiling a flat local Delta table ends cleanly every time. (It passes on 0.9.2 too:
    the hang needs a scan still in flight, see the corrupt-file test below.)"""
    src = ni.write_delta(tmp_path / "flat", ni.flat_table())
    for run in range(20):
        try:
            done = _run_profile(src, tmp_path / "flat.shape")
        except subprocess.TimeoutExpired:  # pragma: no cover - the failure being tested
            pytest.fail(f"run {run}: the process did not end (hang at exit)")
        assert done.returncode == 0, f"run {run} exit {done.returncode}: {done.stderr[-300:]}"
        assert "terminate called" not in done.stderr


def test_a_normal_multi_line_message_numbered_from_zero_is_kept() -> None:
    from shape.cli.errors import one_line

    assert one_line("steps:\n0: first\n1: second") == "steps: 0: first 1: second"
    delta = "Kernel error: bad\n   0: <unknown>\n   1: <unknown>\n  12: start_thread"
    assert one_line(delta) == "Kernel error: bad"


@pytest.mark.parametrize("how", ["flag", "env"])
def test_a_failed_cloud_delta_read_ends_the_process_with_logging_on(
    tmp_path: Path, how: str
) -> None:
    """--log-json / --metrics / SHAPE_LOG_JSON make the command line wait for its log line, which
    used to switch off the exit by itself: a read through deltalake's Python file system that fails
    midway hung the process (10 runs in 10)."""
    from deltalake import write_deltalake

    src = tmp_path / "d"
    for i in range(30):
        part = pa.table({"id": list(range(i * 1000, (i + 1) * 1000))})
        write_deltalake(str(src), part, mode="append")
    argv = "['shape', '--log-json', 'version']" if how == "flag" else "['shape', 'version']"
    script = (
        "import sys, importlib\n"
        "cli = importlib.import_module('shape.cli.main')\n"
        "from deltalake import DeltaTable\n"
        "from shape.io import deltafs\n"
        "def command(argv):\n"
        "    deltafs.note_bridge()\n"
        f"    scan = DeltaTable({str(src)!r}).to_pyarrow_dataset().scanner(batch_size=2)\n"
        "    reader = scan.to_reader()\n"
        "    reader.read_next_batch()\n"
        "    raise OSError('connection lost')\n"
        "cli._dispatch = command\n"
        f"sys.argv = {argv}\n"
        "cli.main()\n"
    )
    env = {**os.environ, "SHAPE_LOG_JSON": "1"} if how == "env" else dict(os.environ)
    for run in range(3):
        try:
            done = subprocess.run(
                [sys.executable, "-c", script],
                capture_output=True,
                text=True,
                timeout=60,
                env=env,
            )
        except subprocess.TimeoutExpired:  # pragma: no cover - the failure being tested
            pytest.fail(f"run {run}: the process did not end")
        assert done.returncode == 2, f"run {run}: exit {done.returncode}: {done.stderr[-300:]}"
        assert "shape: error:" in done.stderr


def test_a_delta_read_that_fails_after_the_scan_started_exits_2_not_hangs(tmp_path: Path) -> None:
    """A data file that is not Parquet fails the read while other files are still being scanned on
    Arrow's pool threads: on 0.9.2 the process then hung at exit (4 runs in 6). The error is one
    line and exit 2."""
    from deltalake import write_deltalake

    src = tmp_path / "d"
    for i in range(30):
        part = pa.table({"id": list(range(i * 1000, (i + 1) * 1000)), "s": ["v"] * 1000})
        write_deltalake(str(src), part, mode="append")
    files = sorted(src.glob("*.parquet"))
    files[15].write_bytes(b"PAR1 not a parquet file")
    for run in range(6):
        try:
            done = _run_profile(src, tmp_path / "d.shape")
        except subprocess.TimeoutExpired:  # pragma: no cover - the failure being tested
            pytest.fail(f"run {run}: the process did not end (hang at exit)")
        assert done.returncode == 2, f"run {run} exit {done.returncode}: {done.stderr[-300:]}"
        errors = [x for x in done.stderr.splitlines() if x.startswith("shape: error:")]
        assert len(errors) == 1, done.stderr
