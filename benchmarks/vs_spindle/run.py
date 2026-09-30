"""One command for the whole comparison against the pinned Spindle: verifiers, then benchmarks.

    source scripts/env.sh && python benchmarks/vs_spindle/run.py --quick  # D1, D2, retail; 3 runs
    source scripts/env.sh && python benchmarks/vs_spindle/run.py --full  # all of 3.4; 5 runs

Order, per workload (equivalence before timing, section 6.4):

1. run the equivalence verifier for the implementation (exit 0 required);
2. run the benchmark, which writes the same output the verifier checks;
3. domain workloads only: run the verifier again on the timed output (``--no-generate``).

A workload's numbers are recorded only when every verifier run for it exited 0; otherwise the
record keeps the verifier status and ``median_s`` is ``null``. The result is written to
``benchmarks/vs_spindle/results.json`` (schema: ``results.schema.json``) with verifier status and
numbers for ``spindle``, ``reference_port`` and ``shape`` (the product: ``shape.profile`` for the
profiling workloads; the generation workloads stay reference_port only until P6). The exit code
is 1 if any verifier failed, else 0.

Everything runs under the exclusive benchmark lock (``$BENCH_OUT_DIR/bench.lock``).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import bench_lock, machine_meta  # noqa: E402
from paths import BENCH_OUT_DIR, SHAPE_PY, SHAPE_ROOT, SPINDLE_PY, SPINDLE_ROOT  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_OUT = HERE / "results.json"
SCHEMA_FILE = HERE / "results.schema.json"
PROFILE = HERE / "profile_1to1"
DOMAIN = HERE / "domain_1to1"
IMPL = "reference_port"

# Workloads (section 3.4). Later work packages add their own (CLI gates, streaming, START).
QUICK_PROFILE_DATASETS = ["d1.csv", "d1.parquet", "d2.csv", "d2.parquet"]
FULL_PROFILE_DATASETS = [
    "d1.csv",
    "d1.parquet",
    "d2.csv",
    "d2.parquet",
    "d3.csv",
    "d3.parquet",
    "d4.csv",
    "d4.parquet",
    "mt",
]
QUICK_DOMAINS = [("retail", ["small", "medium"])]
FULL_DOMAINS = [("retail", ["medium", "large"])]  # every other domain at medium, from P6-01
DATASET_IDS = {
    "d1.csv": "D1",
    "d1.parquet": "D1",
    "d2.csv": "D2",
    "d2.parquet": "D2",
    "d3.csv": "D3",
    "d3.parquet": "D3",
    "d4.csv": "D4",
    "d4.parquet": "D4",
    "mt": "MT",
}


# ─────────────────────────────────────────────────────────────────────────────
# minimal JSON-schema validation (type, enum, const, required, properties,
# additionalProperties, items, minimum, anyOf), so the check needs no extra dependency
# ─────────────────────────────────────────────────────────────────────────────

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(v: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(value: Any, schema: dict, path: str = "$", root: dict | None = None) -> list[str]:
    """Return the list of schema violations (empty when valid)."""
    root = root or schema
    if "$ref" in schema:
        node: Any = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return validate(value, node, path, root)
    if "anyOf" in schema:
        errs = [validate(value, s, path, root) for s in schema["anyOf"]]
        return (
            []
            if any(not e for e in errs)
            else [f"{path}: matches none of anyOf ({errs[0][:1]}...)"]
        )
    out: list[str] = []
    if "const" in schema and value != schema["const"]:
        out.append(f"{path}: expected {schema['const']!r}, got {value!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{path}: {value!r} not in {schema['enum']}")
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]
        if not any(_is_type(value, x) for x in types):
            return out + [f"{path}: expected {types}, got {type(value).__name__}"]
    if "minimum" in schema and _is_type(value, "number") and value < schema["minimum"]:
        out.append(f"{path}: {value} < minimum {schema['minimum']}")
    if isinstance(value, dict):
        for k in schema.get("required", []):
            if k not in value:
                out.append(f"{path}: missing required key {k!r}")
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                out += validate(v, props[k], f"{path}.{k}", root)
            elif schema.get("additionalProperties") is False:
                out.append(f"{path}: unexpected key {k!r}")
            elif isinstance(schema.get("additionalProperties"), dict):
                out += validate(v, schema["additionalProperties"], f"{path}.{k}", root)
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            out += validate(v, schema["items"], f"{path}[{i}]", root)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# steps
# ─────────────────────────────────────────────────────────────────────────────


def sh(cmd: list[str], log: Path | None = None) -> tuple[int, str]:
    """Run a command, stream its output to the console and return (exit code, output)."""
    print("+", " ".join(cmd), flush=True)
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    assert p.stdout is not None
    lines = []
    for ln in p.stdout:
        lines.append(ln)
        if len(lines) <= 400 or ln.startswith(("VERDICT", "COLUMNS", "  NOT", "MISMATCH")):
            sys.stdout.write(ln)
    rc = p.wait()
    text = "".join(lines)
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(text)
    return rc, text


def display_command(cmd: list[str]) -> str:
    """The command with machine paths replaced by the section 1 variables (results.json must
    not contain machine paths)."""
    text = " ".join(cmd)
    for path, var in (
        (str(SHAPE_PY), "$SHAPE_VENV/bin/python"),
        (str(SPINDLE_PY), "$SPINDLE_PY"),
        (str(BENCH_OUT_DIR), "$BENCH_OUT_DIR"),
        (str(SHAPE_ROOT), "$SHAPE_ROOT"),
    ):
        text = text.replace(path, var)
    return text


def verifier_record(cmd: list[str], rc: int) -> dict[str, Any]:
    status = {0: "pass", 1: "fail", 2: "unavailable"}.get(rc, "error")
    return {"status": status, "exit_code": rc, "command": display_command(cmd)}


def git_rev(path: Path) -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def git_dirty(path: Path) -> bool:
    try:
        return bool(
            subprocess.run(
                ["git", "-C", str(path), "status", "--porcelain"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
    except (OSError, subprocess.CalledProcessError):
        return False


def py_version(py: Path, pkg: str) -> str | None:
    code = (
        "import platform; print(platform.python_version())"
        if pkg == "python"
        else f"import importlib.metadata as m; print(m.version('{pkg}'))"
    )
    r = subprocess.run([str(py), "-c", code], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else None


def profile_workloads(datasets: list[str], reps: int, out: dict, impl: str = IMPL) -> bool:
    """Profiling workloads for ``impl`` (``reference_port`` or ``shape``). Returns True if every
    verifier passed. Each implementation is timed against its own Spindle runs in the same job
    (T-19); the ``spindle`` record is the first one written, and every implementation's record
    also carries the Spindle median it was compared with."""
    ok = True
    dpy = str(SHAPE_PY)
    rc, _ = sh([dpy, str(PROFILE / "datasets.py"), *sorted({DATASET_IDS[d] for d in datasets})])
    if rc != 0:
        raise SystemExit(f"datasets.py failed ({rc})")
    vcmd = [dpy, str(PROFILE / "verify.py"), "--impl", impl, *datasets]
    rc, _ = sh(vcmd, BENCH_OUT_DIR / "verify" / f"profile_{impl}.txt")
    ver = verifier_record(vcmd, rc)
    bench_json = BENCH_OUT_DIR / "profile" / f"bench_results_{impl}.json"
    numbers: dict[str, Any] = {}
    if rc == 0:
        bcmd = [
            dpy,
            str(PROFILE / "bench.py"),
            "--impl",
            impl,
            "--reps",
            str(reps),
            "--out",
            str(bench_json),
            *datasets,
        ]
        brc, _ = sh(bcmd)
        if brc != 0:
            raise SystemExit(f"profile bench failed ({brc})")
        numbers = json.loads(bench_json.read_text())
    else:
        ok = False
    for ds in datasets:
        rec_sp: dict[str, Any] = {
            "kind": "profile",
            "dataset": ds,
            "median_s": None,
            "runs_s": [],
            "verifier": None,
        }
        rec_im: dict[str, Any] = {
            "kind": "profile",
            "dataset": ds,
            "verifier": ver,
            "median_s": None,
            "median_s_1t": None,
            "runs_s": [],
            "speedup_vs_spindle": None,
        }
        if ds in numbers:
            n = numbers[ds]
            rec_sp.update(
                median_s=n["spindle"]["median_s"],
                runs_s=n["spindle"]["runs"],
                peak_rss_mb=n["spindle"]["peak_rss_mb"],
            )
            rec_im.update(
                median_s=n["impl_mt"]["median_s"],
                median_s_1t=n["impl_st"]["median_s"],
                runs_s=n["impl_mt"]["runs"],
                peak_rss_mb=n["impl_mt"]["peak_rss_mb"],
                spindle_median_s=n["spindle"]["median_s"],
                speedup_vs_spindle=n["spindle"]["median_s"] / n["impl_mt"]["median_s"],
            )
        out["spindle"]["workloads"].setdefault(f"profile:{ds}", rec_sp)
        out[impl]["workloads"][f"profile:{ds}"] = rec_im
    return ok


def domain_workloads(domain: str, scales: list[str], runs: int, out: dict) -> bool:
    """Generation workloads for one domain. Returns True if every verifier passed."""
    ok = True
    for scale in scales:
        wid = f"generate:{domain}:{scale}"
        vcmd = [
            str(SPINDLE_PY),
            str(DOMAIN / "verify.py"),
            "--domain",
            domain,
            "--scale",
            scale,
            "--impl",
            IMPL,
        ]
        rc, _ = sh(vcmd)
        ver = verifier_record(vcmd, rc)
        rec_sp: dict[str, Any] = {
            "kind": "generate",
            "domain": domain,
            "scale": scale,
            "median_s": None,
            "runs_s": [],
            "verifier": None,
        }
        rec_im: dict[str, Any] = {
            "kind": "generate",
            "domain": domain,
            "scale": scale,
            "verifier": ver,
            "median_s": None,
            "runs_s": [],
            "speedup_vs_spindle": None,
        }
        if rc == 0:
            report = BENCH_OUT_DIR / "bench" / f"{IMPL}_{domain}_{scale}.json"
            bcmd = [
                str(SHAPE_PY),
                str(DOMAIN / "bench.py"),
                "--impl",
                IMPL,
                "--domain",
                domain,
                "--scales",
                scale,
                "--runs",
                str(runs),
                "--report",
                str(report),
            ]
            brc, _ = sh(bcmd)
            if brc != 0:
                raise SystemExit(f"domain bench failed ({brc})")
            # the timed runs wrote the directories the verifier reads: verify that output too
            v2 = [*vcmd, "--no-generate"]
            rc2, _ = sh(v2)
            rec_im["verifier"] = {**verifier_record(v2, rc2), "first_run": ver}
            if rc2 == 0:
                s = json.loads(report.read_text())["scales"][scale]["summary"]
                rec_sp.update(
                    median_s=s["spindle"]["total_s"],
                    runs_s=s["spindle"]["runs_total_s"],
                    gen_s=s["spindle"]["gen_s"],
                    write_s=s["spindle"]["write_s"],
                    rows=s["spindle"]["rows"],
                    peak_rss_mb=s["spindle"]["peak_rss_mb"],
                )
                rec_im.update(
                    median_s=s[IMPL]["total_s"],
                    runs_s=s[IMPL]["runs_total_s"],
                    gen_s=s[IMPL]["gen_s"],
                    write_s=s[IMPL]["write_s"],
                    rows=s[IMPL]["rows"],
                    peak_rss_mb=s[IMPL]["peak_rss_mb"],
                    speedup_vs_spindle=s["spindle"]["total_s"] / s[IMPL]["total_s"],
                )
            else:
                ok = False
        else:
            ok = False
        out["spindle"]["workloads"][wid] = rec_sp
        out[IMPL]["workloads"][wid] = rec_im
    return ok


def other_domain_baselines(runs: int, out: dict) -> None:
    """--full: every other Spindle domain at medium, Spindle side only (reference_port supports
    retail only and reports ``unavailable``; shape stays null until P6-01)."""
    sys.path.insert(0, str(HERE))
    r = subprocess.run(
        [str(SPINDLE_PY), str(HERE / "dump_schema.py")], capture_output=True, text=True
    )
    if r.returncode != 0:
        raise SystemExit(f"dump_schema.py failed:\n{r.stderr}")
    names = sorted({Path(p).name.rsplit("_", 1)[0] for p in r.stdout.split()} - {"retail"})
    for dom in names:
        wid = f"generate:{dom}:medium"
        rec: dict[str, Any] = {
            "kind": "generate",
            "domain": dom,
            "scale": "medium",
            "median_s": None,
            "runs_s": [],
            "verifier": None,
        }
        rc, text = sh(
            [
                str(SPINDLE_PY),
                str(DOMAIN / "generate.py"),
                "--impl",
                "spindle",
                "--domain",
                dom,
                "--scale",
                "medium",
                "--seed",
                "42",
            ]
        )
        if rc == 0:
            times = []
            for _ in range(runs):
                rc, text = sh(
                    [
                        str(SPINDLE_PY),
                        str(DOMAIN / "generate.py"),
                        "--impl",
                        "spindle",
                        "--domain",
                        dom,
                        "--scale",
                        "medium",
                        "--seed",
                        "42",
                    ]
                )
                line = next(x for x in reversed(text.splitlines()) if x.startswith("GEN_JSON "))
                times.append(json.loads(line[9:])["total_s"])
            rec.update(runs_s=times, median_s=sorted(times)[len(times) // 2])
        out["spindle"]["workloads"][wid] = rec
        out[IMPL]["workloads"][wid] = {
            "kind": "generate",
            "domain": dom,
            "scale": "medium",
            "median_s": None,
            "runs_s": [],
            "speedup_vs_spindle": None,
            "verifier": {
                "status": "unavailable",
                "exit_code": 2,
                "command": f"reference_port supports retail only ({dom})",
            },
        }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--quick", action="store_true", help="D1, D2, retail small+medium; 3 runs")
    g.add_argument("--full", action="store_true", help="every workload in section 3.4; 5 runs")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="results JSON to write")
    ap.add_argument("--dry-run", action="store_true", help="print the workloads and exit")
    ap.add_argument(
        "--only",
        choices=("all", "profile", "generate"),
        default="all",
        help="run one family of workloads (the gate for a phase needs only its own)",
    )
    a = ap.parse_args(argv)

    mode = "quick" if a.quick else "full"
    runs = 3 if a.quick else 5
    datasets = QUICK_PROFILE_DATASETS if a.quick else FULL_PROFILE_DATASETS
    domains = QUICK_DOMAINS if a.quick else FULL_DOMAINS
    if a.dry_run:
        print(
            f"mode={mode} runs={runs}\nprofile: {datasets}\ngenerate: {domains}"
            + ("\n+ every other domain at medium (Spindle only)" if a.full else "")
        )
        return 0
    for req, what in (
        (SPINDLE_PY, "Spindle venv"),
        (SPINDLE_ROOT / "sqllocks_spindle", "Spindle checkout"),
        (SHAPE_PY, "Shape venv"),
    ):
        if not Path(req).exists():
            print(
                f"ERROR: {what} not found at {req}; run setup_spindle.sh (section 1)",
                file=sys.stderr,
            )
            return 2

    machine = {
        **machine_meta(),
        "loadavg_start": os.getloadavg()[0] if hasattr(os, "getloadavg") else 0.0,
        "spindle_venv": {
            p: py_version(SPINDLE_PY, p) for p in ("python", "pandas", "numpy", "pyarrow")
        },
        "shape_venv": {p: py_version(SHAPE_PY, p) for p in ("python", "numpy", "pyarrow")},
    }
    out: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "runs": runs,
        "generated_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine": machine,
        "spindle_commit": git_rev(SPINDLE_ROOT),
        "shape_commit": git_rev(SHAPE_ROOT),
        "shape_tree_dirty": git_dirty(SHAPE_ROOT),
        "spindle": {"workloads": {}},
        IMPL: {"workloads": {}},
        "shape": {"workloads": {}},
    }
    ok = True
    with bench_lock():
        if a.only in ("all", "profile"):
            ok &= profile_workloads(datasets, runs, out, IMPL)
            ok &= profile_workloads(datasets, runs, out, "shape")
        if a.only in ("all", "generate"):
            for dom, scales in domains:
                ok &= domain_workloads(dom, scales, runs, out)
            if a.full:
                other_domain_baselines(runs, out)
    schema = json.loads(SCHEMA_FILE.read_text())
    errs = validate(out, schema)
    if errs:
        print(
            "results.json does not match results.schema.json:\n  " + "\n  ".join(errs),
            file=sys.stderr,
        )
        return 3
    Path(a.out).write_text(json.dumps(out, indent=1) + "\n")
    print(f"\nwrote {a.out}")
    for impl in (IMPL, "shape"):
        for wid, rec in out[impl]["workloads"].items():
            sp = rec.get("spindle_median_s", out["spindle"]["workloads"][wid]["median_s"])
            print(
                f"  {impl:14s} {wid:26s} verifier={rec['verifier']['status']:11s} "
                f"spindle={sp if sp is None else round(sp, 2)} "
                f"impl={rec['median_s'] and round(rec['median_s'], 2)} "
                f"speedup={rec['speedup_vs_spindle'] and round(rec['speedup_vs_spindle'], 2)}"
            )
    if not ok:
        print(
            "FAIL: at least one verifier did not pass; its numbers are not recorded",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
