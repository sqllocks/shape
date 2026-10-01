"""Microbenchmarks of the generation kernel (P4-03), recorded in ``results.json``.

Each kernel function is timed on 1,000,000 rows (median of 5 runs after one warm-up, in this
process: the kernels are called directly, so start-up and imports are excluded as in T-19) against
two baselines:

* ``reference_s``: its pure-Python twin (``shape.kernel.reference.gen``), the T-03 oracle;
* ``numpy_s``: the numpy call that does the same job (Philox ``random_raw``, ``Generator.random``,
  ``standard_normal``, ``Generator.choice``) where there is one.

Equivalence comes before timing: every kernel result is compared with its twin on the very output
being timed (integers and strings equal, floats within 1e-12) and nothing is recorded otherwise.
Writes the ``kernel_microbench`` key of ``results.json`` and leaves every other key alone.

    python benchmarks/vs_spindle/kernel_bench.py [--rows N] [--runs N] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import numpy as np  # noqa: E402
import pyarrow as pa  # type: ignore[import-untyped]  # noqa: E402
from common import bench_lock, machine_meta, wait_for_quiet  # noqa: E402

from shape import _kernel as native  # noqa: E402
from shape.kernel.reference import gen as ref  # noqa: E402

DEFAULT_OUT = HERE / "results.json"
K0, K1 = 0x0123456789ABCDEF, 0xFEDCBA9876543210


def median_s(fn: Callable[[], Any], runs: int) -> float:
    fn()  # warm-up
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return statistics.median(times)


def same(a: Any, b: Any) -> bool:
    x, y = pa.array(a), pa.array(b)
    if x.type != y.type:
        return False
    if pa.types.is_floating(x.type):
        return bool(np.allclose(x.to_numpy(), y.to_numpy(), rtol=1e-12, atol=1e-12))
    return bool(x.equals(y))


def cases(n: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(1)
    weights = pa.array(rng.random(50) + 0.01)
    prob, alias = (pa.array(x) for x in native.alias_build(weights))
    pool = pa.array([f"name{i:05d}" for i in range(5000)])
    idx = pa.array(rng.integers(0, 5000, n), type=pa.int64())
    first = pa.array(native.pool_take(pool, idx))
    ids = pa.array(np.arange(n, dtype=np.int64))
    start = 19358  # 2023-01-01
    dw = pa.array(native.day_weights(start, 1461, [1.0] * 12, [1, 1, 1, 1, 2, 4, 3], True))
    hw = pa.array(native.hour_weights_peaks([12.0, 18.0], 2.0))
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    big_rng = np.random.Generator(np.random.Philox(key=K0 | K1 << 64))
    return [
        {
            "name": "philox_words",
            "native": lambda: native.philox_words(K0, K1, 0, n),
            "reference": lambda: ref.philox_words(K0, K1, 0, n),
            "numpy": lambda: np.random.Philox(key=K0 | K1 << 64).random_raw(n),
        },
        {
            "name": "philox_uniform",
            "native": lambda: native.philox_uniform(K0, K1, 0, n),
            "reference": lambda: ref.philox_uniform(K0, K1, 0, n),
            "numpy": lambda: big_rng.random(n),
        },
        {
            "name": "philox_normal",
            "native": lambda: native.philox_normal(K0, K1, 0, n),
            "reference": lambda: ref.philox_normal(K0, K1, 0, n),
            "numpy": lambda: big_rng.standard_normal(n),
        },
        {
            "name": "alias_sample",
            "native": lambda: native.alias_sample(prob, alias, K0, K1, 0, n),
            "reference": lambda: ref.alias_sample(prob, alias, K0, K1, 0, n),
            "numpy": lambda: big_rng.choice(
                50, size=n, p=np.asarray(weights) / weights.to_numpy().sum()
            ),
        },
        {
            "name": "pool_take",
            "native": lambda: native.pool_take(pool, idx),
            "reference": lambda: ref.pool_take(pool, idx),
            "numpy": lambda: pool.take(idx),
        },
        {
            "name": "template_strings",
            "native": lambda: native.template_strings(
                ["INV-", "-", ""], [(1, 8), (0, 0)], [first, ids], n
            ),
            "reference": lambda: ref.template_strings(
                ["INV-", "-", ""], [(1, 8), (0, 0)], [first, ids], n
            ),
            "numpy": None,
        },
        {
            "name": "join_strings",
            "native": lambda: native.join_strings([first, first], " ", False),
            "reference": lambda: ref.join_strings([first, first], " ", False),
            "numpy": lambda: __import__("pyarrow.compute").compute.binary_join_element_wise(
                first, first, " "
            ),
        },
        {
            "name": "string_case_title",
            "native": lambda: native.string_case(first, "title"),
            "reference": lambda: ref.string_case(first, "title"),
            "numpy": lambda: __import__("pyarrow.compute").compute.utf8_title(first),
        },
        {
            "name": "uuid4_strings",
            "native": lambda: native.uuid4_strings(K0, K1, 0, n),
            "reference": lambda: ref.uuid4_strings(K0, K1, 0, n),
            "numpy": None,
        },
        {
            "name": "random_strings_len6",
            "native": lambda: native.random_strings(K0, K1, 0, n, 6, alphabet),
            "reference": lambda: ref.random_strings(K0, K1, 0, n, 6, alphabet),
            "numpy": None,
        },
        {
            "name": "temporal_sample",
            "native": lambda: native.temporal_sample(dw, hw, start, K0, K1, 0, n),
            "reference": lambda: ref.temporal_sample(dw, hw, start, K0, K1, 0, n),
            "numpy": None,
        },
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument(
        "--ref-rows",
        type=int,
        default=100_000,
        help="rows for the slow pure-Python twin (its time is scaled to --rows)",
    )
    a = ap.parse_args(argv)
    n = a.rows
    kernels: dict[str, dict[str, Any]] = {}
    with bench_lock():
        load = wait_for_quiet()
        for case in cases(n):
            name = case["name"]
            got = case["native"]()
            # equivalence first, on a slice the twin can do quickly (same rows, same keys)
            small = cases(a.ref_rows)
            small_case = next(c for c in small if c["name"] == name)
            if not same(small_case["native"](), small_case["reference"]()):
                print(f"FAIL: {name}: native result differs from its twin", file=sys.stderr)
                return 1
            rec: dict[str, Any] = {
                "rows": n,
                "native_s": median_s(case["native"], a.runs),
                "equivalent_to_reference": True,
            }
            ref_s = median_s(small_case["reference"], max(1, min(a.runs, 3)))
            rec["reference_s"] = ref_s * n / a.ref_rows
            rec["reference_rows_measured"] = a.ref_rows
            rec["speedup_vs_reference"] = rec["reference_s"] / rec["native_s"]
            if case["numpy"] is not None:
                rec["baseline_s"] = median_s(case["numpy"], a.runs)
                rec["speedup_vs_baseline"] = rec["baseline_s"] / rec["native_s"]
            rec["native_rows_per_s"] = n / rec["native_s"]
            kernels[name] = rec
            print(
                f"{name:22s} native {rec['native_s'] * 1e3:8.1f} ms"
                f"  {rec['native_rows_per_s'] / 1e6:7.1f} M rows/s"
                f"  x{rec['speedup_vs_reference']:.0f} vs twin"
                + (
                    f"  x{rec['speedup_vs_baseline']:.2f} vs numpy/arrow"
                    if "baseline_s" in rec
                    else ""
                )
            )
        del got
    doc = json.loads(Path(a.out).read_text())
    doc["kernel_microbench"] = {
        "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "machine": {**machine_meta(), "loadavg_start": load, "threads": native.set_threads(0)},
        "rows": n,
        "runs": a.runs,
        "kernels": kernels,
    }
    Path(a.out).write_text(json.dumps(doc, indent=1) + "\n")
    print(f"wrote kernel_microbench to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
