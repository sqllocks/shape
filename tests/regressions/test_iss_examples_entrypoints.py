"""ISS-cli #6 and #8: every file in examples/ runs, and ``python -m shape`` works."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = sorted(p for p in (ROOT / "examples").glob("*.py"))


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src") + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    return subprocess.run(args, cwd=cwd, capture_output=True, text=True, env=env, check=False)


def test_there_are_examples() -> None:
    assert EXAMPLES, "examples/ holds no .py file"


@pytest.mark.parametrize("script", EXAMPLES, ids=lambda p: p.name)
def test_every_example_runs(script: Path, tmp_path: Path) -> None:
    r = _run([sys.executable, str(script)], tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]


def test_profile_accepts_a_list_of_row_dicts() -> None:
    import shape

    prof = shape.profile([{"id": 1, "age": 42}, {"id": 2, "age": 37}, {"id": 3, "age": None}])
    table = next(iter(prof.tables.values()))
    assert table["row_count"] == 3
    assert set(table["columns"]) == {"id", "age"}


def test_profile_row_dicts_equal_the_same_columns() -> None:
    import pyarrow as pa

    import shape

    rows = [{"a": 1, "b": "x"}, {"a": 2, "b": "y"}]
    assert shape.profile(rows) == shape.profile(pa.table({"a": [1, 2], "b": ["x", "y"]}))


def test_profile_still_rejects_a_list_of_non_dicts() -> None:
    import shape
    from shape.profile.reference.sources import SourceError

    with pytest.raises(SourceError, match="unsupported source type list"):
        shape.profile([1, 2, 3])


def test_python_dash_m_shape_version(tmp_path: Path) -> None:
    r = _run([sys.executable, "-m", "shape", "--version"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert r.stdout.startswith("shape ")


def test_python_dash_m_shape_runs_a_command(tmp_path: Path) -> None:
    (tmp_path / "a.csv").write_text("a,b\n1,x\n2,y\n")
    r = _run([sys.executable, "-m", "shape", "profile", "a.csv", "-o", "a.shape"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "a.shape").is_file()
