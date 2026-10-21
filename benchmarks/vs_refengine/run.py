"""Recorded-release verifiers first, then fresh-process candidate/baseline timings."""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from common import bench_lock, machine_meta  # noqa: E402 (harness path bootstrap)
from comparison_contract import (  # noqa: E402
    NOT_COMPARABLE_EXIT,
    recorded_not_comparable_reason,
)
from paths import (  # noqa: E402 (harness path bootstrap)
    BASELINE_PY,
    BASELINE_VERSION,
    BENCH_OUT_DIR,
    SHAPE_PY,
    SHAPE_ROOT,
)
from recorded_store import RecordingError  # noqa: E402 (harness path bootstrap)
from recorded_store import manifest as recording_manifest  # noqa: E402 (harness path bootstrap)

PROFILE = HERE / "profile_1to1"
DOMAIN = HERE / "domain_1to1"
STREAM = HERE / "stream_1to1"
SCHEMA_VERSION = 2
SCHEMA_FILE = HERE / "results.schema.json"
DEFAULT_OUT = HERE / "results.json"
IMPL = "shape"
QUICK_PROFILE_DATASETS = ["d1.csv", "d1.parquet", "d2.csv", "d2.parquet"]
spec = importlib.util.spec_from_file_location("full_recorded_profile_plan", PROFILE / "verify.py")
assert spec and spec.loader
plan_profile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plan_profile)
FULL_PROFILE_DATASETS = plan_profile.ALL
QUICK_DOMAINS = [("retail", ["small", "medium"])]
FULL_DOMAINS = [("retail", ["small", "medium", "large"])]
QUICK_STREAM_SCALES = ["small", "medium"]
FULL_STREAM_SCALES = ["small", "medium"]
FULL_SHAPE_EXTRA_SCALES = {"retail": ["small", "large"]}
FULL_SHAPE_SCALE = "medium"
BASELINE_ONLY = False

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _is_type(v: Any, t: str) -> bool:
    if t == "integer":
        return isinstance(v, int) and not isinstance(v, bool)
    if t == "number":
        return isinstance(v, (int, float)) and not isinstance(v, bool)
    return isinstance(v, _TYPES[t])


def validate(
    value: Any, schema: dict[str, Any], path: str = "$", root: dict[str, Any] | None = None
) -> list[str]:
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
    if rc:
        print(f"command failed: {display_command(cmd)}; exit {rc}", flush=True)
        sys.stdout.write("".join(lines[-200:]))
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
        (str(BASELINE_PY), "$BASELINE_PY"),
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
    r = subprocess.run(
        [str(py), "-c", code],
        env=baseline_environment() if py == BASELINE_PY else None,
        capture_output=True,
        text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def parse_shard(text: str) -> tuple[int, int]:
    """``K/N`` with 1 <= K <= N."""
    try:
        k, n = (int(x) for x in text.split("/"))
    except ValueError:
        raise argparse.ArgumentTypeError(f"--shard wants K/N, got {text!r}") from None
    if not 1 <= k <= n:
        raise argparse.ArgumentTypeError(f"--shard wants 1 <= K <= N, got {text!r}")
    return k, n


def select_units(units: list[Any], shard: tuple[int, int] | None) -> list[Any]:
    """Round-robin part ``K`` of ``N`` of ``units`` (all of them without a shard)."""
    if shard is None:
        return list(units)
    k, n = shard
    return [u for i, u in enumerate(units) if i % n == k - 1]


def py_kernel(py: Path) -> str | None:
    try:
        p = subprocess.run(
            [str(py), "-c", "from shape.kernel import kernel_name; print(kernel_name())"],
            env=baseline_environment() if py == BASELINE_PY else None,
            capture_output=True,
            text=True,
        )
        return p.stdout.strip() if p.returncode == 0 else None
    except OSError:
        return None


def baseline_environment() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    return env


def baseline_domains() -> list[str]:
    return sorted(
        p.stem.removesuffix("_3nf") for p in (HERE / "fixtures" / "schemas").glob("*_3nf.json")
    )


def generate_plan(full: bool, domains: list[str] | None) -> list[tuple[str, str, list[str]]]:
    if not full:
        return [("shape", d, list(sc)) for d, sc in QUICK_DOMAINS]
    assert domains is not None
    return [
        ("shape", d, ["small", "medium", "large"] if d == "retail" else ["medium"]) for d in domains
    ]


def expected_ids(
    datasets: list[str], gen: list[tuple[str, str, list[str]]], stream_scales: list[str], only: str
) -> dict[str, list[str]]:
    ids = []
    if only in ("all", "profile"):
        ids += ["profile:" + d for d in datasets]
    if only in ("all", "generate"):
        ids += ["generate:" + d + ":" + sc for _, d, scales in gen for sc in scales]
    if only in ("all", "stream"):
        ids += ["stream:retail:order:" + sc for sc in stream_scales]
    return {"shape": ids}


def blank(kind: str, verifier: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
    return {
        "kind": kind,
        "median_s": None,
        "runs_s": [],
        "verifier": verifier,
        "ratio_vs_baseline": None,
        **kwargs,
    }


def put_numbers(
    base: dict[str, Any],
    current: dict[str, Any],
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> None:
    base.update(median_s=baseline["median_s"], runs_s=baseline["runs_s"])
    current.update(
        median_s=candidate["median_s"],
        runs_s=candidate["runs_s"],
        baseline_median_s=baseline["median_s"],
        ratio_vs_baseline=baseline["median_s"] / candidate["median_s"],
    )


def profile_group(ds: str) -> str:
    if ds.startswith("edge/x_"):
        return "EDGE2"
    if ds.startswith("edge/"):
        return "EDGE"
    if ds.startswith("mt"):
        return "MT"
    return ds.split(".")[0].upper()


def profile_workloads(
    datasets: list[str], reps: int, out: dict[str, Any], impl: str = "shape"
) -> bool:
    py = BASELINE_PY if BASELINE_ONLY else SHAPE_PY
    rc, _ = sh(
        [str(py), str(PROFILE / "datasets.py"), *sorted({profile_group(ds) for ds in datasets})]
    )
    if rc:
        return False
    ok = True
    for ds in datasets:
        vcmd = [str(py), str(PROFILE / "verify.py"), "--impl", "shape", ds]
        rc, _ = sh(vcmd)
        ver = verifier_record(vcmd, rc)
        if rc == NOT_COMPARABLE_EXIT:
            reason = recorded_not_comparable_reason("profile:" + ds)
            if reason is not None:
                ver.update(status="not_comparable", workload="profile:" + ds, reason=reason)
        base = blank("profile", ver, dataset=ds)
        current = blank("profile", ver, dataset=ds)
        out["baseline"]["workloads"]["profile:" + ds] = base
        if not BASELINE_ONLY:
            out["shape"]["workloads"]["profile:" + ds] = current
        if ver["status"] == "not_comparable":
            continue
        if rc:
            ok = False
            continue
        report = BENCH_OUT_DIR / "profile" / ("bench_" + ds.replace("/", "_") + ".json")
        cmd = [
            str(py),
            str(PROFILE / "bench.py"),
            "--impl",
            "shape",
            "--reps",
            str(reps),
            "--out",
            str(report),
            ds,
        ]
        if BASELINE_ONLY:
            cmd.append("--baseline-only")
        brc, _ = sh(cmd)
        if brc:
            if BASELINE_ONLY:
                raise RecordingError(f"profile:{ds}: timing worker failed (exit {brc})")
            ok = False
            continue
        values = json.loads(report.read_text())[ds]
        b = values["baseline"]
        base.update(median_s=b["median_s"], runs_s=b["runs"])
        if not BASELINE_ONLY:
            c = values["impl_mt"]
            put_numbers(
                base,
                current,
                {"median_s": b["median_s"], "runs_s": b["runs"]},
                {"median_s": c["median_s"], "runs_s": c["runs"]},
            )
            current["median_s_1t"] = values["impl_st"]["median_s"]
    return ok


def domain_workloads(
    domain: str, scales: list[str], runs: int, out: dict[str, Any], impl: str = "shape"
) -> bool:
    ok = True
    py = BASELINE_PY if BASELINE_ONLY else SHAPE_PY
    for scale in scales:
        wid = "generate:" + domain + ":" + scale
        vcmd = [
            str(py),
            str(DOMAIN / "verify.py"),
            "--impl",
            "shape",
            "--domain",
            domain,
            "--scale",
            scale,
        ]
        rc, _ = sh(vcmd)
        ver = verifier_record(vcmd, rc)
        base = blank("generate", ver, domain=domain, scale=scale)
        current = blank("generate", ver, domain=domain, scale=scale)
        out["baseline"]["workloads"][wid] = base
        if not BASELINE_ONLY:
            out["shape"]["workloads"][wid] = current
        if rc:
            ok = False
            continue
        report = BENCH_OUT_DIR / "bench" / (domain + "_" + scale + ".json")
        cmd = [
            str(py),
            str(DOMAIN / "bench.py"),
            "--impl",
            "shape",
            "--domain",
            domain,
            "--scales",
            scale,
            "--runs",
            str(runs),
            "--report",
            str(report),
        ]
        if BASELINE_ONLY:
            cmd.append("--baseline-only")
        brc, _ = sh(cmd)
        if brc:
            if BASELINE_ONLY:
                raise RecordingError(f"{wid}: timing worker failed (exit {brc})")
            print(wid + ": a timed run failed; no numbers", file=sys.stderr)
            ok = False
            continue
        second = [*vcmd, "--no-generate"]
        rc2, _ = sh(second)
        base["verifier"] = {**verifier_record(second, rc2), "first_run": ver}
        current["verifier"] = base["verifier"]
        if rc2:
            ok = False
            continue
        values = json.loads(report.read_text())["scales"][scale]["summary"]
        b = values["baseline"]
        base.update(median_s=b["total_s"], runs_s=b["runs_total_s"])
        if not BASELINE_ONLY:
            c = values["shape"]
            put_numbers(
                base,
                current,
                {"median_s": b["total_s"], "runs_s": b["runs_total_s"]},
                {"median_s": c["total_s"], "runs_s": c["runs_total_s"]},
            )
    return ok


def stream_workloads(scales: list[str], runs: int, out: dict[str, Any]) -> bool:
    ok = True
    py = BASELINE_PY if BASELINE_ONLY else SHAPE_PY
    for scale in scales:
        wid = "stream:retail:order:" + scale
        cmd = [str(py), str(STREAM / "verify.py"), "--scale", scale]
        rc, _ = sh(cmd)
        ver = verifier_record(cmd, rc)
        base = blank("stream", ver, domain="retail", scale=scale)
        current = blank("stream", ver, domain="retail", scale=scale)
        out["baseline"]["workloads"][wid] = base
        if not BASELINE_ONLY:
            out["shape"]["workloads"][wid] = current
        if rc:
            ok = False
            continue
        report = BENCH_OUT_DIR / "bench" / ("stream_" + scale + ".json")
        bcmd = [
            str(py),
            str(STREAM / "bench.py"),
            "--scale",
            scale,
            "--runs",
            str(runs),
            "--report",
            str(report),
        ]
        if BASELINE_ONLY:
            bcmd.append("--baseline-only")
        brc, _ = sh(bcmd)
        if brc:
            if BASELINE_ONLY:
                raise RecordingError(f"{wid}: timing worker failed (exit {brc})")
            ok = False
            continue
        second = [*cmd, "--no-generate"]
        rc2, _ = sh(second)
        base["verifier"] = {**verifier_record(second, rc2), "first_run": ver}
        current["verifier"] = base["verifier"]
        if rc2:
            ok = False
            continue
        values = json.loads(report.read_text())
        b = values["summary"]["baseline"]
        base.update(median_s=b["total_s"], runs_s=b["runs_total_s"])
        if not BASELINE_ONLY:
            c = values["summary"]["shape"]
            put_numbers(
                base,
                current,
                {"median_s": b["total_s"], "runs_s": b["runs_total_s"]},
                {"median_s": c["total_s"], "runs_s": c["runs_total_s"]},
            )
            current["cli_end_to_end_s"] = values.get("cli_end_to_end_s")
            current["cli_ratio_vs_baseline"] = values.get("cli_ratio_vs_baseline")
    return ok


def keep_other_families(path: Path, only: str, out: dict[str, Any]) -> None:
    try:
        old = json.loads(path.read_text())
    except (OSError, ValueError):
        return
    if (
        old.get("schema_version") != SCHEMA_VERSION
        or old.get("baseline", {}).get("commit") != out["baseline"]["commit"]
    ):
        return
    for tool in ("baseline", "shape"):
        for wid, rec in old.get(tool, {}).get("workloads", {}).items():
            if rec.get("kind") != only:
                out[tool]["workloads"].setdefault(wid, rec)


def main(argv: list[str] | None = None) -> int:
    global BASELINE_ONLY
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--quick", action="store_true")
    g.add_argument("--full", action="store_true")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--only", choices=("all", "profile", "generate", "stream"), default="all")
    ap.add_argument("--shard", type=parse_shard)
    ap.add_argument("--domain", action="append")
    ap.add_argument("--dataset", action="append")
    ap.add_argument("--baseline-only", action="store_true")
    a = ap.parse_args(argv)
    BASELINE_ONLY = a.baseline_only
    datasets = list(QUICK_PROFILE_DATASETS if a.quick else FULL_PROFILE_DATASETS)
    if a.dataset:
        if set(a.dataset) - set(datasets):
            ap.error("dataset outside the selected mode")
        datasets = [d for d in datasets if d in a.dataset]
    domains = baseline_domains()
    gen = generate_plan(a.full, domains) if a.only in ("all", "generate") else []
    if a.domain:
        gen = [u for u in gen if u[1] in a.domain]
    gen = select_units(gen, a.shard)
    stream_scales = QUICK_STREAM_SCALES if a.quick else FULL_STREAM_SCALES
    if a.dry_run:
        print(
            f"mode={'quick' if a.quick else 'full'} runs={3 if a.quick else 5} "
            "implementations=shape,baseline"
        )
        print(f"profile: {datasets if a.only in ('all', 'profile') else '-'}")
        print(f"generate: {gen}")
        print(f"stream: {stream_scales if a.only in ('all', 'stream') else '-'}")
        return 0
    expected = expected_ids(datasets, gen, stream_scales, a.only)
    commit = (
        subprocess.run(
            ["git", "rev-parse", "v" + BASELINE_VERSION + "^{commit}"],
            cwd=SHAPE_ROOT,
            capture_output=True,
            text=True,
        ).stdout.strip()
        or "unknown"
    )
    out: dict[str, Any] = {
        "schema_version": 2,
        "mode": "quick" if a.quick else "full",
        "runs": 3 if a.quick else 5,
        "generated_utc": dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "machine": machine_meta(),
        "baseline": {
            "kind": "shape",
            "version": BASELINE_VERSION,
            "commit": commit,
            "workloads": {},
        },
        "shape_commit": git_rev(SHAPE_ROOT),
        "shape_tree_dirty": git_dirty(SHAPE_ROOT),
        "shape": {"workloads": {}},
        "selection": {
            "only": a.only,
            "shard": None if a.shard is None else f"{a.shard[0]}/{a.shard[1]}",
            "domains": a.domain,
            "datasets": a.dataset,
            "baseline_domains": domains,
            "expected": expected,
        },
    }
    unavailable = None
    try:
        metadata = recording_manifest()
        out["baseline"]["commit"] = metadata["shape_commit"]
        if not BASELINE_PY.is_file() or not SHAPE_PY.is_file():
            raise RecordingError("baseline or candidate interpreter is unavailable")
        if py_version(BASELINE_PY, "sqllocks-shape") != BASELINE_VERSION:
            raise RecordingError("baseline version is not " + BASELINE_VERSION)
        if py_version(BASELINE_PY, "pyarrow") != py_version(SHAPE_PY, "pyarrow"):
            raise RecordingError("candidate and baseline PyArrow versions differ")
        if py_kernel(BASELINE_PY) != py_kernel(SHAPE_PY) or py_kernel(BASELINE_PY) not in (
            "rust",
            "python",
        ):
            raise RecordingError("candidate and baseline kernels differ")
    except (RecordingError, OSError) as exc:
        unavailable = str(exc)
    if unavailable:
        for wid in expected["shape"]:
            parts = wid.split(":")
            labels = (
                {"dataset": parts[1]}
                if parts[0] == "profile"
                else {"domain": parts[1], "scale": parts[-1]}
            )
            rec = blank(
                wid.split(":")[0],
                {"status": "unavailable", "exit_code": 2, "command": "recorded_store.load"},
                **labels,
            )
            out["baseline"]["workloads"][wid] = dict(rec)
            if not BASELINE_ONLY:
                out["shape"]["workloads"][wid] = dict(rec)
        print(unavailable, file=sys.stderr)
        rc = 2
    else:
        out["machine"]["baseline_venv"] = {
            pkg: py_version(BASELINE_PY, pkg) for pkg in ("python", "numpy", "pyarrow")
        }
        out["machine"]["shape_venv"] = {
            pkg: py_version(SHAPE_PY, pkg) for pkg in ("python", "numpy", "pyarrow")
        }
        ok = True
        with bench_lock():
            if a.only in ("all", "profile"):
                ok &= profile_workloads(datasets, out["runs"], out)
            if a.only in ("all", "generate"):
                for impl, domain, scales in gen:
                    ok &= domain_workloads(domain, scales, out["runs"], out, impl)
            if a.only in ("all", "stream"):
                ok &= stream_workloads(stream_scales, out["runs"], out)
        rc = 0 if ok else 1
    if a.only != "all":
        keep_other_families(Path(a.out), a.only, out)
    try:
        out["kernel_microbench"] = json.loads(Path(a.out).read_text())["kernel_microbench"]
    except (OSError, ValueError, KeyError):
        pass
    errors = validate(out, json.loads(SCHEMA_FILE.read_text()))
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 3
    dest = Path(a.out)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, sort_keys=True, indent=2) + "\n")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
