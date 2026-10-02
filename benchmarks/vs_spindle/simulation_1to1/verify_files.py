"""Simulation parity verifier: Shape's simulators against the pinned baseline's (P6-04).

Runs in the *baseline* venv (it needs pandas and scipy); each side's code runs in its own venv,
through ``files_baseline_worker.py`` and ``files_shape_worker.py``:

    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/simulation_1to1/verify_files.py \\
        [--case NAME ...] [--negative-control] [--out REPORT.json]

Every ``files_case_<simulator>.py`` in this directory is one case (a module per simulator, so the
lanes that port different simulators never edit the same file). A case runs two kinds of check:

* **Mechanism parity (exact).** Both tools get the *same* input tables (the baseline's retail at
  seed 42), the same configuration and the same seed. The simulators draw their random numbers in
  the same order, so the outputs must be equal: the same files, the same rows in the same order,
  the same manifests. The harness maps the baseline's names to Shape's (``files_common.NAME_MAP``,
  D-13) before it compares and records the map in the report. Wall-clock and random values (ids,
  times) are checked for form, never for equality.
* **T-21 parity.** Each tool simulates its own generated retail tables, the baseline at the
  reference seed 42 and Shape at 1042, with the baseline's own spread from seeds 43-46: the output
  columns satisfy T-21 (a)-(e) (names and order, null rate, KS, TVD, vocabulary) and the output
  counts lie within max(5 sigma, 1.5 x the baseline's range). The seed set is fixed; there is no
  option for another, and the verifier refuses to run if the constants were edited.

**Allow-list.** Where the baseline has a trust-harming defect, Shape fixes it (owner's standing
decision, 2026-10-01) and the difference is a named entry of the case's ``ALLOWED``. Each entry is
shown by a *probe*: the baseline's defective behaviour is observed, and so is Shape's fix; an entry
the baseline no longer shows fails the run (a stale entry), and any difference that is not allowed
fails it.

``--negative-control`` tampers with Shape's output (a value, a file, a count, a name) and requires
every tampering to be caught; it exits 1 if one goes undetected.

Exit codes: 0 every check passed, 1 a check failed, 2 an input is missing or a worker did not run.
"""

from __future__ import annotations

import argparse
import importlib
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import files_common as sc  # noqa: E402
from paths import BENCH_OUT_DIR, SHAPE_PY, SPINDLE_PY  # noqa: E402

if not (sc.BASELINE_SEEDS == (43, 44, 45, 46) and sc.REF_SEED == 42 and sc.SHAPE_SEED == 1042):
    sys.exit(
        "the T-21 seed set is fixed (baseline 42 + 43-46, Shape 1042); a verdict from any "
        "other counts for nothing"
    )


class MissingInput(Exception):
    """An input is missing or a worker did not run (exit code 2)."""


class Ctx:
    """What a case gets: the scale, the retail inputs and a way to run one job on either side."""

    def __init__(self, scale: str, tables: list[str]) -> None:
        self.scale = scale
        self.tables = tables
        self.seeds = sc.BASELINE_SEEDS
        self.ref_seed = sc.REF_SEED
        self.shape_seed = sc.SHAPE_SEED

    def input_dir(self, tool: str, seed: int) -> Path:
        """The retail tables a tool generated at ``seed`` (Parquet, ``<table>.parquet``)."""
        import files_compare

        gen = files_compare.dv().generate
        return gen.out_dir(
            "spindle" if tool == "baseline" else "shape", sc.DOMAIN, self.scale, seed
        )

    @property
    def exact_dir(self) -> Path:
        """The shared input of the mechanism parity checks: the baseline's retail, seed 42."""
        return self.input_dir("baseline", sc.REF_SEED)

    def run(self, side: str, sim: str, name: str, **params: Any) -> dict[str, Any]:
        """Run job ``name`` of simulator ``sim`` on ``side`` (``baseline`` or ``shape``) in a
        clean output directory; the result is the dict the side returned, plus ``out_dir``. A
        simulator that raised is a result with an ``error`` key (a case may expect it)."""
        out = sc.work_dir(sim, name, side)
        if out.exists():
            import shutil

            shutil.rmtree(out)
        out.mkdir(parents=True)
        job = {
            "sim": sim,
            "name": name,
            "side": side,
            "out_dir": str(out),
            "scale": self.scale,
            **params,
        }
        job_file = out.parent / f"{side}.job.json"
        sc.write_json(job_file, job)
        py = SPINDLE_PY if side == "baseline" else SHAPE_PY
        worker = HERE / (
            "files_baseline_worker.py" if side == "baseline" else "files_shape_worker.py"
        )
        proc = subprocess.run([str(py), str(worker), str(job_file)], capture_output=True, text=True)
        result_file = out / "_result.json"
        if proc.returncode != 0 or not result_file.exists():
            raise MissingInput(
                f"{side} worker for {sim}/{name} exited {proc.returncode}:\n{proc.stderr[-2000:]}"
            )
        result = sc.read_json(result_file)
        result["out_dir"] = str(out)
        return result


def ensure_inputs(scale: str) -> list[str]:
    """Generate the retail runs the cases read (the baseline at 42-46, Shape at 1042) when "
    "missing."""
    import files_compare

    dv = files_compare.dv()
    raw = dv.load_schema_json(sc.DOMAIN)
    tables = list(raw["tables"])
    runs = [("spindle", s) for s in (sc.REF_SEED, *sc.BASELINE_SEEDS)] + [("shape", sc.SHAPE_SEED)]
    for impl, seed in runs:
        if not dv.ensure_run(impl, sc.DOMAIN, scale, seed, tables):
            raise MissingInput(f"could not generate {impl} {sc.DOMAIN}/{scale}/seed{seed}")
    return tables


def discover(names: list[str]) -> list[Any]:
    mods = []
    for path in sorted(HERE.glob("files_case_*.py")):
        mod = importlib.import_module(path.stem)
        if not hasattr(mod, "baseline_side"):  # a pattern case (P6-04b), run by verify_patterns.py
            continue
        if names and mod.NAME not in names:
            continue
        mods.append(mod)
    if names:
        unknown = set(names) - {m.NAME for m in mods}
        if unknown:
            raise MissingInput(f"no case for {', '.join(sorted(unknown))}")
    return mods


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--scale",
        choices=["small"],
        default="small",
        help="retail scale of the inputs (small: the baseline slices a frame once per slot, so a "
        "medium table would take it hours)",
    )
    ap.add_argument(
        "--case",
        action="append",
        default=[],
        metavar="NAME",
        help="run only this case (repeatable)",
    )
    ap.add_argument(
        "--negative-control",
        action="store_true",
        help="tamper with Shape's output and require every change to be caught",
    )
    ap.add_argument("--list", action="store_true", help="list the cases and exit")
    ap.add_argument(
        "--out",
        default=None,
        help="report JSON (default: $BENCH_OUT_DIR/simulation/report_<scale>.json)",
    )
    a = ap.parse_args(argv)
    try:
        cases = discover(a.case)
        if a.list:
            for m in cases:
                print(m.NAME)
            return 0
        t0 = time.time()
        tables = ensure_inputs(a.scale)
        ctx = Ctx(a.scale, tables)
        report: dict[str, Any] = {
            "scale": a.scale,
            "reference_seed": sc.REF_SEED,
            "baseline_seeds": list(sc.BASELINE_SEEDS),
            "shape_seed": sc.SHAPE_SEED,
            "name_map_baseline_to_shape": sc.NAME_MAP,
            "cases": {},
        }
        failed = False
        for mod in cases:
            fn = mod.negative_controls if a.negative_control else mod.run
            t1 = time.time()
            checks = fn(ctx)
            for c in checks.items:
                print(c.line(), flush=True)
            report["cases"][mod.NAME] = {
                "ok": checks.ok,
                "seconds": round(time.time() - t1, 1),
                "allowed": getattr(mod, "ALLOWED", {}),
                "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in checks.items],
            }
            failed |= not checks.ok
        report["ok"] = not failed
        report["seconds"] = round(time.time() - t0, 1)
    except MissingInput as exc:
        print(f"MISSING: {exc}", file=sys.stderr)
        return 2
    out = (
        Path(a.out)
        if a.out
        else BENCH_OUT_DIR
        / "simulation"
        / f"report_{a.scale}{'_negative' if a.negative_control else ''}.json"
    )
    sc.write_json(out, report)
    label = "negative control" if a.negative_control else "verify"
    print(f"{label}: {'ALL PASSED' if not failed else 'FAILED'} ({report['seconds']}s) -> {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
