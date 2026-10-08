"""#768: portable math (``shape.kernel.pmath``) gives the same bits on every machine.

The functions use IEEE basic operations only, so their results cannot depend on the CPU or the C
library. Checked here: the constants are fdlibm's; ``log`` and ``exp`` are within an ulp and
``log1p`` within two of the exact value (40-digit ``decimal``), the others accurate; the native
kernel and the numpy twin agree bit for bit; numpy's CPU dispatch does not change them
(``NPY_DISABLE_CPU_FEATURES``, in a child process); and known-answer digests of the inputs and
results over a fixed grid, the same on every platform CI runs.
"""

from __future__ import annotations

import decimal
import hashlib
import json
import math
import os
import subprocess
import sys
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

from shape.kernel import dispatch, pmath
from shape.kernel.reference import pmath as ref

FDLIBM_DECIMALS = {
    "LG1": 6.666666666666735130e-01,
    "LG2": 3.999999999940941908e-01,
    "LG3": 2.857142874366239149e-01,
    "LG4": 2.222219843214978396e-01,
    "LG5": 1.818357216161805012e-01,
    "LG6": 1.531383769920937332e-01,
    "LG7": 1.479819860511658591e-01,
    "LN2_HI": 6.93147180369123816490e-01,
    "LN2_LO": 1.90821492927058770002e-10,
    "INVLN2": 1.44269504088896338700e00,
    "O_THRESHOLD": 7.09782712893383973096e02,
    "U_THRESHOLD": -7.45133219101941108420e02,
    "P1": 1.66666666666666019037e-01,
    "P2": -2.77777777770155933842e-03,
    "P3": 6.61375632143793436117e-05,
    "P4": -1.65339022054652515390e-06,
    "P5": 4.13813679705723846039e-08,
    "S1": -1.66666666666666324348e-01,
    "S2": 8.33333333332248946124e-03,
    "S3": -1.98412698298579493134e-04,
    "S4": 2.75573137070700676789e-06,
    "S5": -2.50507602534068634195e-08,
    "S6": 1.58969099521155010221e-10,
    "C1": 4.16666666666666019037e-02,
    "C2": -1.38888888888741095749e-03,
    "C3": 2.48015872894767294178e-05,
    "C4": -2.75573143513906633035e-07,
    "C5": 2.08757232129817482790e-09,
    "C6": -1.13596475577881948265e-11,
    "TWO_PI": 2.0 * math.pi,
}


def _ulps(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return np.abs(a.view(np.int64) - b.view(np.int64))


def _uniform(rng: np.random.Generator, low: float, high: float, n: int) -> np.ndarray:
    """``low + (high - low) * u`` as separate numpy operations: ``Generator.uniform`` is C code
    that a compiler may fuse into one multiply-add on arm64, which gives other inputs there."""
    return low + (high - low) * rng.random(n)


def _grid() -> dict[str, np.ndarray]:
    """Inputs for every function: a fixed spread plus the edges, built with exact or basic
    operations only (no transcendental function, no fusable C code)."""
    rng = np.random.default_rng(768)
    pos = np.concatenate(
        [
            np.ldexp(_uniform(rng, 0.5, 1.0, 20_000), rng.integers(-1074, 1024, 20_000)),
            rng.random(20_000),
            1.0 + _uniform(rng, -1e-6, 1e-6, 2_000),
            np.arange(1, 4_001, dtype=np.float64) * 2.0**-53,
            [5e-324, 2.2250738585072014e-308, 0.5, 1.0, 2.0, math.sqrt(2.0), 1e308, 0.0],
        ]
    )
    return {
        "pos": pos,
        "exp": np.concatenate(
            [_uniform(rng, -750.0, 712.0, 20_000), _uniform(rng, -1.0, 1.0, 10_000), [0.0, -0.0]]
        ),
        "turns": np.concatenate(
            [np.arange(0, 1 << 14, dtype=np.float64) / (1 << 14), _uniform(rng, -3.0, 3.0, 5_000)]
        ),
        "lgamma": np.concatenate([_uniform(rng, 1e-3, 50.0, 5_000), np.arange(1.0, 2_000.0)]),
        "log1p": np.concatenate(
            [_uniform(rng, -0.999, 3.0, 5_000), -np.arange(1, 2_000) * 2.0**-53]
        ),
    }


def _hex(values: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(values, dtype="<f8").tobytes()).hexdigest()


def _interp_points() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    xp = -2.0 + np.arange(41, dtype=np.float64) * 0.125
    xp = xp * xp * xp
    fp = np.cumsum(np.arange(41, dtype=np.float64) % 7 - 2.5)
    q = -9.0 + np.arange(5_001, dtype=np.float64) * (37.0 / 5_000)
    return q, xp, fp


def _digests() -> dict[str, str]:
    """A digest of the inputs and of each function's results over them."""
    g = _grid()
    q, xp, fp = _interp_points()
    out = {f"input:{name}": _hex(v) for name, v in g.items()}
    out["input:interp"] = _hex(np.concatenate([q, xp, fp]))
    out.update(
        {
            "log": _hex(pmath.log(g["pos"])),
            "exp": _hex(pmath.exp(g["exp"])),
            "pow": _hex(pmath.pow(g["pos"][:30_000], -1.0 / 2.7)),
            "cos_turns": _hex(pmath.cos_turns(g["turns"])),
            "lgamma": _hex(pmath.lgamma(g["lgamma"])),
            "log1p": _hex(pmath.log1p(g["log1p"])),
            "interp": _hex(pmath.interp(q, xp, fp)),
        }
    )
    return out


# The digests of _digests() (both kernel modes, every platform). A function's digest changes only
# when its algorithm changes, and then every pinned dataset id that uses it changes too
# (docs/GENERATION_STABILITY.md).
KNOWN_DIGESTS: dict[str, str] = {
    "input:pos": "147e53ad22a586c04ca4c3da84c842ffd367014bece93708476fa9ed8e1b1ca9",
    "input:exp": "98f3973f76c7c489e94a28a75e91c76eef0761ed2936e726d39adb0d440d3f92",
    "input:turns": "5f9b5ce28bf5b3243af4f5115a2a415093181fadcdeecf989c6358faf53a9a44",
    "input:lgamma": "6b367453dd98bbbe3bbe14907dbf2673a2e8c548d26f69e8bfd1c8049fca4ed5",
    "input:log1p": "677ee6865b82bcce88111354eb5b11156254463b705b2e0b66d8f3fb4a82f00b",
    "input:interp": "b66c9cd7a5a34a43f73c24aaea75755e9833a90a57122c8950db96f7b99e6452",
    "log": "e8ff694d719ef72ace699ecab91fe2c37d7f98b3958bd09b144ab7cadf287307",
    "exp": "f92718e2d23e2922c83f1922b51d4410c07673129ae134228a9f10631bc9ea30",
    "pow": "0011b109c93ebf6adff9b28e7df81793f52ced9383ceab999a799d6ca9817a88",
    "cos_turns": "15065bcae0b287a7d29efddacac2c7b9d786f95f2195c2df9d6182ae1361be7e",
    "lgamma": "796a5cb0b5c4481e3c6ab1847916b2fe39f6a5bcd9b1cd15047bd3b230eb48c1",
    "log1p": "77655514ef63759427b5fc36b49773038a04caed3857780361ddfcf59c35996d",
    "interp": "f1574e1dfa8808d4ebbb39a45aa5629fee19484afc294dc6d7fe0e8c8c63ca3f",
}


@pytest.fixture(params=["python", "rust"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    yield request.param
    dispatch.reset()


def test_the_constants_are_fdlibms() -> None:
    for name, value in FDLIBM_DECIMALS.items():
        assert getattr(ref, name) == value, name


def _exact(f: Any, xs: np.ndarray) -> np.ndarray:
    """``f`` in 40-digit decimal arithmetic, rounded once to float64: the reference, the same on
    every platform (C libraries differ from each other by an ulp or two)."""
    with decimal.localcontext() as ctx:
        ctx.prec = 40
        return np.array([float(f(decimal.Decimal(float(x)))) for x in xs])


def test_log_exp_and_log1p_are_within_an_ulp_or_two_of_the_exact_value() -> None:
    g = _grid()
    x = g["pos"][g["pos"] > 0]
    assert _ulps(ref.log(x), _exact(lambda d: d.ln(), x)).max() <= 1
    e = g["exp"][(g["exp"] > -708.0) & (g["exp"] < 709.0)]
    assert _ulps(ref.exp(e), _exact(lambda d: d.exp(), e)).max() <= 1
    lx = g["log1p"]
    assert _ulps(ref.log1p(lx), _exact(lambda d: (1 + d).ln(), lx)).max() <= 2


def test_cos_turns_pow_lgamma_interp_are_accurate() -> None:
    g = _grid()
    t = g["turns"]
    exact = np.array([math.cos(2.0 * math.pi * v) for v in t])  # its argument is rounded too
    assert np.abs(ref.cos_turns(t) - exact).max() < 4e-15
    for t0, want in [(0.0, 1.0), (0.25, 0.0), (0.5, -1.0), (0.75, 0.0), (0.125, math.sqrt(0.5))]:
        assert abs(float(ref.cos_turns(t0)[0]) - want) <= 2.3e-16
    x = g["pos"][(g["pos"] > 1e-300) & (g["pos"] < 1e300)]
    for y in (-1.0 / 2.7, 0.5, 3.0, 1.0 / 0.3):
        got = ref.pow(x, y)
        want = np.array([v**y if abs(y * math.log(v)) < 700.0 else math.nan for v in x])
        ok = np.isfinite(want) & (want > 1e-300) & (want < 1e300)
        bound = (2.0 + np.abs(y * np.log(x[ok]))) * 2.0**-52
        assert (np.abs(got[ok] / want[ok] - 1.0) <= bound).all(), y
    gx = g["lgamma"]
    want = np.array([math.lgamma(v) for v in gx])
    assert (np.abs(ref.lgamma(gx) - want) <= 1e-14 * np.maximum(np.abs(want), 1.0)).all()
    q, xp, fp = _interp_points()
    q = np.concatenate([q, xp, [np.nan]])
    np.testing.assert_allclose(ref.interp(q, xp, fp), np.interp(q, xp, fp), rtol=1e-15, atol=1e-14)


def test_special_values() -> None:
    nan, inf = math.nan, math.inf
    np.testing.assert_array_equal(
        ref.log([0.0, -0.0, inf, 1.0, -1.0, nan]), [-inf, -inf, inf, 0.0, nan, nan]
    )
    np.testing.assert_array_equal(
        ref.exp([0.0, 710.0, -746.0, inf, -inf, nan]), [1.0, inf, 0.0, inf, 0.0, nan]
    )
    np.testing.assert_array_equal(ref.pow([0.0, 1.0, 4.0, -1.0], 0.5), [0.0, 1.0, 2.0, nan])
    np.testing.assert_array_equal(ref.pow([0.0, 2.0, nan], -1.0), [inf, 0.5, nan])
    np.testing.assert_array_equal(ref.pow([0.0, 3.0, nan, -2.0], 0.0), [1.0, 1.0, nan, nan])
    x = np.array([0.0, 3.0, 0.1, 7.5, 1e300])
    np.testing.assert_array_equal(ref.pow(x, 1.0), x)
    np.testing.assert_array_equal(ref.pow(x, 2.0), x * x)
    np.testing.assert_array_equal(ref.pow(x, 0.5), np.sqrt(x))
    np.testing.assert_array_equal(ref.pow(x, -1.0), [np.inf, 1 / 3.0, 1 / 0.1, 1 / 7.5, 1e-300])
    np.testing.assert_array_equal(ref.cos_turns([inf, nan, 2.0**60]), [nan, nan, 1.0])
    np.testing.assert_array_equal(ref.lgamma([1.0, 2.0, 0.0, -1.0, inf]), [0.0, 0.0, nan, nan, inf])
    np.testing.assert_array_equal(ref.log1p([0.0, -1.0, inf, 1e-300]), [0.0, -inf, inf, 1e-300])
    assert ref.exp_scalar(1000.0) == inf and ref.exp_scalar(-1000.0) == 0.0
    assert math.isnan(ref.exp_scalar(nan))
    np.testing.assert_array_equal(ref.interp([0.5, 3.0], [1.0], [7.0]), [7.0, 7.0])
    with pytest.raises(ValueError):
        ref.interp([1.0], [1.0, 2.0], [1.0])


def test_the_scalar_exp_is_the_array_exp() -> None:
    e = _grid()["exp"]
    scalar = np.array([ref.exp_scalar(float(v)) for v in e])
    assert scalar.tobytes() == ref.exp(e).tobytes()


def test_the_native_kernel_gives_the_same_bits() -> None:
    nat = pytest.importorskip("shape._kernel")
    g = _grid()

    def same(native: pa.Array, twin: pa.Array) -> None:
        a = np.asarray(pa.array(native).to_numpy(zero_copy_only=False))
        b = np.asarray(pa.array(twin).to_numpy(zero_copy_only=False))
        assert a.tobytes() == b.tobytes()

    pos = pa.array(np.concatenate([g["pos"], [-1.0, math.inf, math.nan, -0.0]]))
    same(nat.pm_log(pos), ref.pm_log(pos))
    ex = pa.array(np.concatenate([g["exp"], [math.inf, -math.inf, math.nan, 709.7, -745.0]]))
    same(nat.pm_exp(ex), ref.pm_exp(ex))
    for y in (-1.0 / 2.7, 0.5, 3.0, 0.0, -1.0, 1.0, 2.0, math.nan):
        same(nat.pm_pow(pos, y), ref.pm_pow(pos, y))
    tt = pa.array(np.concatenate([g["turns"], [math.inf, math.nan, 2.0**60, -(2.0**49)]]))
    same(nat.pm_cos_turns(tt), ref.pm_cos_turns(tt))
    for fn in ("pm_log", "pm_exp", "pm_cos_turns"):
        with pytest.raises(ValueError):
            getattr(nat, fn)(pa.array([1, 2]))
        with pytest.raises(ValueError):
            getattr(ref, fn)(pa.array([1.0, None]))


def test_the_known_answer_digest(kernel: str) -> None:
    assert _digests() == KNOWN_DIGESTS


def test_numpys_cpu_dispatch_does_not_change_the_results() -> None:
    # The AVX-512 routines off (on a CPU without them, or another architecture, the variable
    # names a feature that is not there, which numpy ignores): the digest is the same.
    here = os.path.dirname(__file__)
    code = (
        f"import json, sys; sys.path.insert(0, {here!r}); import test_pmath as t; "
        "print(json.dumps(t._digests()))"
    )
    digests = {}
    for label, extra in (("default", {}), ("no-avx512", {"NPY_DISABLE_CPU_FEATURES": "X86_V4"})):
        env = {**os.environ, **extra}
        done = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
        )
        assert done.returncode == 0, done.stderr
        digests[label] = json.loads(done.stdout.strip().splitlines()[-1])
    assert digests["default"] == digests["no-avx512"] == KNOWN_DIGESTS
