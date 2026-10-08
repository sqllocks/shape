"""W8-04b (#768): pinned dataset ids and golden bytes do not depend on the CPU or the C library.

numpy computes float64 ``log``, ``exp``, ``log1p``, ``power``, ``cos`` (and more) with AVX-512
routines on some x86 CPUs and with the C library elsewhere, and the C libraries of Linux, macOS and
Windows differ in the last bit too. Generation computes those functions with
:mod:`shape.kernel.pmath` instead, so W1-15's pinned fixtures and W8-04's golden byte corpus hold
on every machine. Checked here:

* the pinned dataset id of every pinned spec, and the golden byte corpus, in a child process with
  numpy's AVX-512 code switched off (``NPY_DISABLE_CPU_FEATURES=X86_V4``) and in one without, in
  both kernel modes: the same ids, and the committed ones;
* a tripwire: generating every pinned spec and every golden-corpus spec calls none of the
  machine-dependent functions of numpy or ``math`` (each call is recorded with where it came from).
  This is what makes the result the same on macOS, Windows and Linux, which a single machine cannot
  run side by side.
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import numpy as np
import pinned_support as ps
import pytest

from shape.kernel import dispatch

ROOT = Path(__file__).resolve().parents[2]
NO_AVX512 = {"NPY_DISABLE_CPU_FEATURES": "X86_V4"}

_CHILD = """
import json, sys
sys.path.insert(0, sys.argv[1])
import pinned_support as ps
with ps.datasets():
    ids = {stem: ps.dataset_id_of(ps.load_spec(stem)) for stem in ps.stems()}
print(json.dumps(ids, sort_keys=True))
"""


def _run(args: list[str], extra: dict[str, str]) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k != "NPY_DISABLE_CPU_FEATURES"}
    return subprocess.run(
        [sys.executable, *args],
        env={**env, **extra},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        cwd=ROOT,
    )


def _ids(kernel: str, extra: dict[str, str]) -> dict[str, str]:
    done = _run(["-c", _CHILD, str(Path(__file__).parent)], {"SHAPE_KERNEL": kernel, **extra})
    assert done.returncode == 0, done.stderr
    ids: dict[str, str] = json.loads(done.stdout.strip().splitlines()[-1])
    return ids


@pytest.mark.parametrize("kernel", ["python", "rust"])
def test_pinned_ids_are_the_same_without_numpys_avx512_code(kernel: str) -> None:
    if kernel == "rust":
        pytest.importorskip("shape._kernel")
    default = _ids(kernel, {})
    without = _ids(kernel, NO_AVX512)
    assert without == default
    expected = ps.load_expected()["specs"]
    assert set(default) == set(expected)
    for stem, found in default.items():
        assert found in {e["id"] for e in expected[stem]}, stem


@pytest.mark.parametrize("kernel", ["python", "rust"])
def test_the_golden_bytes_match_without_numpys_avx512_code(kernel: str) -> None:
    if kernel == "rust":
        pytest.importorskip("shape._kernel")
    done = _run([str(ROOT / "scripts" / "golden_bytes.py")], {"SHAPE_KERNEL": kernel, **NO_AVX512})
    assert done.returncode == 0, done.stdout + done.stderr
    assert "files match the golden byte corpus" in done.stdout


# ---- the tripwire -------------------------------------------------------------------------------

# Results that depend on the CPU (numpy's dispatch) or the C library (numpy's fallback, ``math``).
NUMPY_FUNCTIONS = (
    "log", "log1p", "log2", "log10", "logaddexp", "logaddexp2", "exp", "expm1", "exp2", "power",
    "float_power", "cos", "sin", "tan", "arccos", "arcsin", "arctan", "arctan2", "cosh", "sinh",
    "tanh", "arccosh", "arcsinh", "arctanh", "cbrt", "hypot", "interp",
)  # fmt: skip
MATH_FUNCTIONS = (
    "log", "log1p", "log2", "log10", "exp", "expm1", "pow", "cos", "sin", "tan", "acos", "asin",
    "atan", "atan2", "cosh", "sinh", "tanh", "acosh", "asinh", "atanh", "cbrt", "hypot", "erf",
    "erfc", "gamma", "lgamma",
)  # fmt: skip


def _recorder(calls: list[str], name: str, real: Callable[..., Any]) -> Callable[..., Any]:
    src = str(ROOT / "src")

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        frame = sys._getframe(1)
        where = frame.f_code.co_filename
        if where.startswith(src) or "/plugins/" in where.replace(os.sep, "/"):
            calls.append(
                f"{name} at {Path(os.path.relpath(where, ROOT)).as_posix()}:{frame.f_lineno}"
            )
        return real(*args, **kwargs)

    return wrapped


@pytest.fixture
def tripwire(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    calls: list[str] = []
    for name in NUMPY_FUNCTIONS:
        monkeypatch.setattr(np, name, _recorder(calls, f"numpy.{name}", getattr(np, name)))
    for name in MATH_FUNCTIONS:
        monkeypatch.setattr(math, name, _recorder(calls, f"math.{name}", getattr(math, name)))
    yield calls


@pytest.fixture(params=["python", "rust"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


def test_the_tripwire_records_a_call(tripwire: list[str]) -> None:
    from shape.builtins.strategies import formula  # a module under src/ that calls numpy

    assert formula.np.log is np.log
    _ = np.log(np.array([2.0]))  # from the tests: not recorded
    assert tripwire == []
    exec(compile("np.exp(1.0)", str(ROOT / "src" / "shape" / "probe.py"), "exec"), {"np": np})
    assert tripwire == ["numpy.exp at src/shape/probe.py:1"]


def test_generation_calls_no_machine_dependent_function(
    kernel: str, tripwire: list[str], tmp_path: Path
) -> None:
    sys.path.insert(0, str(ROOT / "scripts"))
    try:
        import golden_bytes as gb
    finally:
        sys.path.remove(str(ROOT / "scripts"))
    with ps.datasets():
        for stem in ps.stems():
            ps.dataset_id_of(ps.load_spec(stem))
    gb.write_all(tmp_path)
    assert not tripwire, "\n".join(sorted(set(tripwire)))
