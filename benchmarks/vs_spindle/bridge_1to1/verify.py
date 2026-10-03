"""Bridge parity (P6-11): ``shape bridge`` against the pinned baseline's JSON bridge for the 17
commands of the plan. Runs in the Shape venv; the baseline runs in its own venv as a child process.

    source scripts/env.sh && "$SHAPE_PY" benchmarks/vs_spindle/bridge_1to1/verify.py \\
        [--scale small|medium] [--skip-data] [--negative-control]

Both bridges are driven as a client drives them: a JSON line in, a JSON line out. For retail:

* **Metadata is compared exactly** (``compare.py``): ``list``, ``describe`` (every table, column,
  type, key, relationship, rule and scale preset), ``dry_run`` (every preset), ``validate`` (the
  baseline's schema file and the same schema as Shape reads it, valid and invalid), ``profile_info``
  (ratios and every distribution), and the shapes of ``preview``, ``generate`` and the stream and
  job commands.
* **Generated data is compared under T-21**, with the equivalence verifier of the domain harness
  (``domain_1to1/verify.py``, clauses (a)-(h), baseline seeds 42-46, Shape seed 1042) run on the
  *bridge's own output*: ``generate`` to Parquet, and ``scale_generate`` in ``local_single`` and
  ``local_mp``. The baseline side of every comparison is also produced through the baseline's
  bridge.
* **Baseline defects fixed** (``ALLOW``, the standing decision of 2026-10-01): each entry names the
  defect and the reason, and the harness *shows* it in the baseline at the pinned commit and shows
  Shape correct, so an entry cannot outlive its defect.
* ``demo_*`` run ``shape demo``: the catalog, the result of a run (same fields, same score and
  artifact count) and the manifest keys are compared with the baseline's, in an isolated home
  for each bridge; an unknown session is compared as the baseline reports it (an error in a
  success) against Shape's ``input.invalid_value``.
* ``fabric_spark`` needs a live Fabric workspace: its refusals are compared, its submit/status/
  cancel is tested against recorded interactions (``tests/bridge/test_scale.py``), not here.

``--negative-control`` proves each comparison can fail: it mutates a Shape result and requires the
same comparison to flag it. Exit codes: 0 every check holds (and every control was flagged),
1 a check failed, 2 a bridge could not be run.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import compare  # noqa: E402
from paths import BENCH_OUT_DIR, SPINDLE_PY  # noqa: E402
from sessions import Baseline, Shape  # noqa: E402

DOMAIN = "retail"
REF_SEED, SPREAD_SEEDS, IMPL_SEED = 42, (43, 44, 45, 46), 1042
DOMAIN_VERIFY = HERE.parent / "domain_1to1" / "verify.py"
ROOT = BENCH_OUT_DIR / "bridge_1to1"
PLAN_COMMANDS = (
    "list", "describe", "generate", "dry_run", "validate", "preview", "profile_info", "demo_list",
    "demo_run", "demo_status", "demo_cleanup", "scale_generate", "stream", "stream_status",
    "stream_stop", "scale_status", "scale_cancel",
)  # fmt: skip

#: The baseline defects Shape fixes (standing owner decision, 2026-10-01). Each is shown below.
ALLOW = {
    "BR-1": "scale_generate local_mp writes the wrong number of rows: the baseline passes the sum "
    "of "
    "all tables' rows as the chunk budget, so order and order_line come out larger than the preset "
    "(medium: order 786,160 where the preset says 500,000). Shape: exact counts in every mode.",
    "BR-2": "jobs are lost with the process: the baseline keeps them in memory, so scale_status "
    "and "
    "scale_cancel answer job_not_found for any job after a restart of the bridge. Shape keeps one "
    "versioned file per job, readable by a new process.",
    "BR-3": "stream rows_written is chunks x chunk_size whatever was written: a stream of 2 chunks "
    "of 20 rows per table wrote 360 rows and said 40. Shape counts the rows written.",
    "BR-4": "generate with a file format and no output_dir succeeds and writes nothing, so a "
    "caller "
    "believes files exist. Shape refuses (usage.missing_argument).",
    "BR-5": "preview with a table name that does not exist returns an empty result as a success. "
    "Shape refuses (input.invalid_value) and names the table.",
    "BR-6": "a profile_info distribution key names a column that does not exist "
    "(promotion.promotion_type: the column is promo_type), so its weights apply to nothing. Shape "
    "reports the weights under the real column.",
    "BR-7": "profile_info reports weights generation never uses: the product_status lifecycle is "
    "introduced 0.08 / discontinued 0.17 in the profile, while the schema generates with 0.10 / "
    "0.15. Shape reports the weights it generates with.",
}  # fmt: skip
ALIASES = {"promotion.promotion_type": "promotion.promo_type"}
SKIPPED = ("product.product_status",)  # BR-7: compared against the schema, not the profile


class Report:
    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []
        self.notes: dict[str, Any] = {}

    def check(self, command: str, name: str, problems: list[str] | bool, detail: str = "") -> bool:
        ok = (not problems) if isinstance(problems, list) else bool(problems)
        text = detail or ("; ".join(problems[:6]) if isinstance(problems, list) else "")
        self.checks.append({"command": command, "check": name, "ok": ok, "detail": text})
        print(
            f"  {'PASS' if ok else 'FAIL'}  {command:<15} {name}"
            + (f"  [{text}]" if text and not ok else ""),
            flush=True,
        )
        return ok


# ─── retail through both bridges ─────────────────────────────────────────────────────────────


def metadata(base: Baseline, shape: Shape, rep: Report) -> dict[str, Any]:
    """list, describe, dry_run, profile_info, preview; returns the results (for the controls)."""
    got: dict[str, Any] = {}
    ok_b, lb = base.call("list")
    ok_s, ls = shape.call("list")
    assert ok_b and ok_s, (lb, ls)
    base_domains = {d["name"]: d for d in lb["domains"]}
    shape_domains = {d["name"]: d for d in ls["domains"]}
    problems = [
        f"shape domain {n!r} is not a baseline domain"
        for n in shape_domains
        if n not in base_domains
    ]
    for name, sd in shape_domains.items():
        bd = base_domains.get(name)
        if bd and (
            bd["profiles"] != sd["profiles"]
            or bd["description"].split(" ")[0] != sd["description"].split(" ")[0]
        ):
            problems.append(f"list: {name}: baseline {bd}, shape {sd}")
    rep.check("list", "every Shape domain is a baseline domain, with its profiles", problems)
    rep.notes["domains_not_in_shape"] = sorted(set(base_domains) - set(shape_domains))
    rep.check(
        "list", "retail is listed", DOMAIN in shape_domains and ls["count"] == len(ls["domains"])
    )

    for mode in ("3nf", "star"):
        _, b = base.call("describe", domain=DOMAIN, mode=mode)
        _, s = shape.call("describe", domain=DOMAIN, mode=mode)
        rep.check(
            "describe",
            f"{DOMAIN} {mode}: tables, columns, keys, relationships, rules, presets",
            compare.describe(b, s),
        )
        got[f"describe_{mode}"] = (b, s)

    problems = []
    for scale in sorted(b["scales"]):
        _, bd = base.call("dry_run", domain=DOMAIN, scale=scale)
        _, sd = shape.call("dry_run", domain=DOMAIN, scale=scale)
        problems += compare.dry_run(bd, sd)
        got.setdefault("dry_run", (bd, sd))
    rep.check(
        "dry_run",
        f"every preset ({', '.join(sorted(b['scales']))}): planned rows and order",
        problems,
    )

    _, bp = base.call("profile_info", domain=DOMAIN)
    _, sp = shape.call("profile_info", domain=DOMAIN)
    rep.check(
        "profile_info",
        "ratios and every baseline distribution",
        compare.profile_info(bp, sp, ALIASES, SKIPPED),
    )
    rep.notes["profile_info_extra_keys"] = sorted(
        set(sp["distribution_keys"]) - {ALIASES.get(k, k) for k in bp["distribution_keys"]}
    )
    got["profile_info"] = (bp, sp)
    schema_phases = _generated_phases()
    base_phases = {
        p["name"]: p["weight"] for p in bp["distributions"]["product.product_status"]["phases"]
    }
    shape_phases = {
        p["name"]: p["weight"] for p in sp["distributions"]["product.product_status"]["phases"]
    }
    rep.check(
        "profile_info",
        "BR-7 the baseline's lifecycle weights are not the ones its schema generates with",
        base_phases != schema_phases,
        f"profile {base_phases}, schema {schema_phases}",
    )
    rep.check(
        "profile_info",
        "BR-7 Shape reports the weights it generates with (the baseline schema's)",
        shape_phases == schema_phases,
        f"shape {shape_phases}, schema {schema_phases}",
    )
    rep.check(
        "profile_info",
        "BR-6: the baseline names a column that does not exist",
        ("promotion.promotion_type" in bp["distributions"])
        and ("promotion.promotion_type" not in sp["distributions"])
        and ("promotion.promo_type" in sp["distributions"])
        and (not any(k.startswith("promotion.promotion_type") for k in sp["distribution_keys"])),
    )

    _, bv = base.call("preview", domain=DOMAIN, rows=30, seed=7)
    _, sv = shape.call("preview", domain=DOMAIN, rows=30, seed=7)
    rep.check("preview", "tables, columns, row counts and value types", compare.preview(bv, sv))
    got["preview"] = (bv, sv)
    problems = []
    for table, column in (
        ("customer", "gender"),
        ("customer", "loyalty_tier"),
        ("store", "store_type"),
    ):
        _, b_all = base.call("preview", domain=DOMAIN, rows=1000, tables=[table], seed=3)
        _, s_all = shape.call("preview", domain=DOMAIN, rows=1000, tables=[table], seed=3)
        b_vals = {r[column] for r in b_all["tables"][table]["data"]}
        s_vals = {r[column] for r in s_all["tables"][table]["data"]}
        problems += (
            []
            if b_vals == s_vals
            else [f"{table}.{column}: baseline {sorted(b_vals)}, shape {sorted(s_vals)}"]
        )
    rep.check("preview", "categorical columns carry the same values", problems)
    return got


def _generated_phases() -> dict[str, float]:
    """The product_status weights in the baseline's own schema dump (what generation runs from)."""
    raw_path = BENCH_OUT_DIR / "schemas" / f"{DOMAIN}_3nf.json"
    if not raw_path.exists():
        subprocess.run(
            [str(SPINDLE_PY), str(HERE.parent / "dump_schema.py"), DOMAIN],
            check=True,
            capture_output=True,
        )
    raw = json.loads(raw_path.read_text())
    phases: dict[str, float] = raw["tables"]["product"]["columns"]["product_status"]["generator"][
        "phases"
    ]
    return phases


def validate_cmd(base: Baseline, shape: Shape, rep: Report, work: Path) -> dict[str, Any]:
    sys.path.insert(0, str(HERE.parent))
    import schema_import

    raw_path = BENCH_OUT_DIR / "schemas" / f"{DOMAIN}_3nf.json"
    if not raw_path.exists():
        subprocess.run(
            [str(SPINDLE_PY), str(HERE.parent / "dump_schema.py"), DOMAIN],
            check=True,
            capture_output=True,
        )
    raw = json.loads(raw_path.read_text())
    got: dict[str, Any] = {}

    def run(label: str, raw_doc: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
        bpath, spath = work / f"{label}.spindle.json", work / f"{label}.shape.json"
        bpath.write_text(json.dumps(raw_doc))
        spath.write_text(json.dumps(schema_import.to_native(raw_doc)))
        return (
            base.call("validate", schema_path=str(bpath)),
            shape.call("validate", schema_path=str(spath)),
            bpath,
            spath,
        )

    (b_ok, b), (s_ok, s), *_ = run("good", raw)
    rep.check(
        "validate",
        "a good schema: valid, table and relationship counts",
        b_ok and s_ok and compare.validate(b, s),
    )
    got["good"] = (b, s)
    broken = json.loads(json.dumps(raw))
    broken["relationships"][0]["parent"] = "no_such_table"
    (b_ok, b), (s_ok, s), *_ = run("broken", broken)
    both_refuse = (b_ok and b["valid"] is False) or not b_ok
    shape_refuses = s_ok and s["valid"] is False and bool(s["errors"])
    rep.check(
        "validate",
        "a schema with a relationship to a missing table is invalid in both",
        both_refuse and shape_refuses,
        f"baseline {b if not b_ok else b['valid']}, shape {s}",
    )
    got["broken"] = (b, s)
    b_ok, b = base.call("validate", schema_path=str(work / "nope.json"))
    s_ok, s = shape.call("validate", schema_path=str(work / "nope.json"))
    rep.check(
        "validate",
        "a missing file is an error in both",
        (not b_ok) and (not s_ok) and s["code"] == "input.not_found",
    )
    return got


# ─── generated data under T-21 ──────────────────────────────────────────────────────────────


def make_run_dir(
    root: Path, impl: str, scale: str, seed: int, source: Path, order: list[str], layout: str
) -> Path:
    """Bridge output in the layout ``domain_1to1/verify.py`` reads."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    run = root / impl / DOMAIN / scale / f"seed{seed}"
    shutil.rmtree(run, ignore_errors=True)
    run.mkdir(parents=True)
    rows: dict[str, int] = {}
    for table in order:
        files = (
            [source / f"{table}.parquet"]
            if layout == "files"
            else sorted((source / table).glob("part-*.parquet"))
        )
        data = pa.concat_tables([pq.read_table(f) for f in files])
        pq.write_table(data, run / f"{table}.parquet", compression="snappy")
        rows[table] = data.num_rows
    (run / "_SUCCESS").write_text(
        json.dumps({"impl": impl, "domain": DOMAIN, "scale": scale, "seed": seed, "rows": rows})
    )
    return run


def t21(root: Path, scale: str, label: str) -> int:
    """The domain verifier on ``root`` (its ``spindle`` and ``shape`` run directories are the
    bridge outputs); returns its exit code."""
    report = root / f"{label}_report.json"
    env = {**os.environ, "BENCH_OUT_DIR": str(root)}
    cmd = [
        str(SPINDLE_PY),
        str(DOMAIN_VERIFY),
        "--domain",
        DOMAIN,
        "--scale",
        scale,
        "--impl",
        "shape",
        "--out",
        str(report),
    ]
    done = subprocess.run(cmd, env=env, capture_output=True, text=True)
    (root / f"{label}.log").write_text(done.stdout + "\n" + done.stderr)
    return done.returncode


def data_checks(
    base: Baseline, shape: Shape, rep: Report, scale: str, work: Path, got: dict[str, Any]
) -> None:
    sys.path.insert(0, str(HERE.parent))
    _, plan = base.call("dry_run", domain=DOMAIN, scale=scale)
    order = plan["generation_order"]
    total = plan["total_rows"]

    # generate: summary, files, then T-21 on the Parquet the two bridges wrote
    _, bg = base.call("generate", domain=DOMAIN, scale=scale, seed=REF_SEED)
    _, sg = shape.call("generate", domain=DOMAIN, scale=scale, seed=IMPL_SEED)
    rep.check(
        "generate",
        f"summary at {scale}: counts per table, totals, integrity",
        compare.generate_summary(bg, sg),
    )
    got["generate"] = (bg, sg)
    for fmt in ("csv", "jsonl", "parquet", "tsv"):
        bd, sd = work / f"b_{fmt}", work / f"s_{fmt}"
        _, b = base.call(
            "generate", domain=DOMAIN, scale="small", seed=1, format=fmt, output_dir=str(bd)
        )
        _, s = shape.call(
            "generate", domain=DOMAIN, scale="small", seed=1, format=fmt, output_dir=str(sd)
        )
        rep.check(
            "generate",
            f"{fmt}: the same file names",
            compare.generate_files(b, s, str(bd), str(sd)),
        )

    root = work / "t21_generate"
    for seed in (REF_SEED, *SPREAD_SEEDS):
        out = work / f"bg{seed}"
        ok, r = base.call(
            "generate", domain=DOMAIN, scale=scale, seed=seed, format="parquet", output_dir=str(out)
        )
        assert ok, r
        make_run_dir(root, "spindle", scale, seed, out, order, "files")
    out = work / "sg"
    ok, r = shape.call(
        "generate",
        domain=DOMAIN,
        scale=scale,
        seed=IMPL_SEED,
        format="parquet",
        output_dir=str(out),
    )
    assert ok, r
    make_run_dir(root, "shape", scale, IMPL_SEED, out, order, "files")
    rep.check(
        "generate",
        f"T-21 on the bridges' own Parquet at {scale} (domain verifier, clauses a-h)",
        t21(root, scale, "generate") == 0,
    )

    # scale_generate: local_single, local_mp
    root = work / "t21_scale"
    base_result: dict[str, Any] = {}
    for seed in (REF_SEED, *SPREAD_SEEDS):
        out = work / f"bs{seed}"
        ok, r = base.call(
            "scale_generate",
            domain=DOMAIN,
            scale=scale,
            seed=seed,
            scale_mode="local_single",
            sinks=["parquet"],
            sink_config={"parquet": {"output_dir": str(out)}},
        )
        assert ok, r
        if seed == REF_SEED:
            base_result = r
        make_run_dir(root, "spindle", scale, seed, out, order, "parts")
    for mode in ("local_single", "local_mp"):
        out = work / f"ss_{mode}"
        ok, sr = shape.call(
            "scale_generate",
            domain=DOMAIN,
            scale=scale,
            seed=IMPL_SEED,
            scale_mode=mode,
            sinks=["parquet"],
            sink_config={"parquet": {"output_dir": str(out)}},
        )
        assert ok, sr
        rep.check(
            "scale_generate",
            f"{mode}: result fields and exact rows",
            compare.scale_generate({**base_result, "scale_mode": mode}, sr, total),
        )
        make_run_dir(root, "shape", scale, IMPL_SEED, out, order, "parts")
        rep.check(
            "scale_generate",
            f"{mode}: T-21 on the Parquet part files at {scale} (clauses a-h)",
            t21(root, scale, mode) == 0,
        )
        _, mem = shape.call("scale_generate", domain=DOMAIN, scale=scale, seed=1, scale_mode=mode)
        rep.check(
            "scale_generate",
            f"{mode}: the default memory sink",
            mem["sinks_written"] == {"memory": "ok"} and mem["rows_generated"] == total,
        )
    for bad in (
        {"domain": "nope"},
        {"domain": DOMAIN, "scale_mode": "warp"},
        {"domain": DOMAIN, "sinks": ["warp"]},
    ):
        b_ok, _b = base.call("scale_generate", **bad)
        s_ok, s = shape.call("scale_generate", **bad)
        rep.check(
            "scale_generate",
            f"both refuse {bad}",
            (not b_ok) and (not s_ok),
            f"baseline ok={b_ok}, shape {s}",
        )
    b_ok, _b = base.call("scale_generate", domain=DOMAIN, scale_mode="fabric_spark")
    s_ok, s = shape.call("scale_generate", domain=DOMAIN, scale_mode="fabric_spark")
    rep.check(
        "scale_generate",
        "fabric_spark without ids and a token is refused in both",
        (not b_ok) and (not s_ok),
    )


def stream_checks(base: Baseline, shape: Shape, rep: Report, work: Path) -> None:
    args = {
        "domain": DOMAIN,
        "scale": "small",
        "seed": 5,
        "interval_seconds": 0,
        "chunk_size": 20,
        "max_chunks": 2,
    }
    bdir, sdir = work / "bstream", work / "sstream"
    b_ok, b = base.call(
        "stream", sinks=["parquet"], sink_config={"parquet": {"output_dir": str(bdir)}}, **args
    )
    s_ok, s = shape.call(
        "stream", sinks=["parquet"], sink_config={"parquet": {"output_dir": str(sdir)}}, **args
    )
    rep.check(
        "stream",
        "starts: stream_id and status started",
        b_ok and s_ok and b["status"] == s["status"] == "started" and bool(s["stream_id"]),
    )
    states = []
    for session, ident in ((base, b["stream_id"]), (shape, s["stream_id"])):
        deadline = time.time() + 120
        while True:
            _, st = session.call("stream_status", stream_id=ident)
            if not st["running"] or time.time() > deadline:
                break
            time.sleep(0.1)
        states.append(st)
    rep.check(
        "stream_status", "keys, chunks_written, running, error", compare.stream_status(*states)
    )

    import pyarrow.parquet as pq

    def rows_on_disk(root: Path) -> int:
        return sum(
            pq.ParquetFile(f).metadata.num_rows
            for f in glob.glob(f"{root}/**/*.parquet", recursive=True)
        )

    b_disk, s_disk = rows_on_disk(bdir), rows_on_disk(sdir)
    rep.check(
        "stream",
        "same rows on disk: chunk_size per table per chunk",
        b_disk == s_disk,
        f"baseline {b_disk}, shape {s_disk}",
    )
    rep.check(
        "stream_status",
        "BR-3 baseline rows_written is chunks x chunk_size, not the rows written",
        states[0]["rows_written"] == 40 and b_disk > 40,
    )
    rep.check(
        "stream_status",
        "BR-3 Shape's rows_written is the rows written",
        states[1]["rows_written"] == s_disk,
    )

    args = {**args, "interval_seconds": 30, "max_chunks": None}
    b_ok, b = base.call("stream", **args)
    s_ok, s = shape.call("stream", **args)
    for session, ident in ((base, b["stream_id"]), (shape, s["stream_id"])):
        deadline = time.time() + 60
        while (
            session.call("stream_status", stream_id=ident)[1]["chunks_written"] < 1
            and time.time() < deadline
        ):
            time.sleep(0.05)
    sb = base.call("stream_stop", stream_id=b["stream_id"])[1]
    ss = shape.call("stream_stop", stream_id=s["stream_id"])[1]
    rep.check(
        "stream_stop",
        "a running stream is stopped in both",
        sb["status"] == ss["status"] == "stopped"
        and sb["stream_id"] == b["stream_id"]
        and ss["stream_id"] == s["stream_id"],
    )
    after = [
        base.call("stream_status", stream_id=b["stream_id"])[1],
        shape.call("stream_status", stream_id=s["stream_id"])[1],
    ]
    rep.check(
        "stream_status",
        "after a stop: not running (the baseline forgets a stopped stream; Shape keeps it)",
        after[0].get("running") is not True
        and after[1]["running"] is False
        and after[1]["status"] == "cancelled",
    )

    for command, key in (
        ("stream_status", "stream_id"),
        ("stream_stop", "stream_id"),
        ("scale_status", "job_id"),
        ("scale_cancel", "job_id"),
    ):
        b_ok, b = base.call(command, **{key: "no-such-id"})
        s_ok, s = shape.call(command, **{key: "job-ffffffffffff"})
        rep.check(
            command,
            "an unknown id is reported (baseline: an error in a success; Shape: input.unknown_job)",
            isinstance(b, dict)
            and "error" in b
            and (not s_ok)
            and s["code"] == "input.unknown_job",
        )


def allow_list(base: Baseline, shape: Shape, rep: Report, work: Path, scale: str) -> None:
    import pyarrow.parquet as pq

    # BR-1: local_mp row counts, at medium (where the baseline's chunk budget goes wrong)
    _, plan = base.call("dry_run", domain=DOMAIN, scale="medium")
    out = work / "br1"
    ok, r = base.call(
        "scale_generate",
        domain=DOMAIN,
        scale="medium",
        scale_mode="local_mp",
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(out)}},
    )
    assert ok, r
    counts = {
        t: sum(pq.ParquetFile(f).metadata.num_rows for f in glob.glob(f"{out}/{t}/*.parquet"))
        for t in plan["planned_rows"]
    }
    wrong = {t: n for t, n in counts.items() if n != plan["planned_rows"][t]}
    rep.check(
        "scale_generate",
        "BR-1 baseline local_mp writes the wrong row counts (medium)",
        bool(wrong),
        f"wrong tables {wrong}",
    )
    out = work / "br1s"
    ok, sr = shape.call(
        "scale_generate",
        domain=DOMAIN,
        scale="medium",
        scale_mode="local_mp",
        sinks=["parquet"],
        sink_config={"parquet": {"output_dir": str(out)}},
    )
    s_counts = {
        t: sum(pq.ParquetFile(f).metadata.num_rows for f in glob.glob(f"{out}/{t}/*.parquet"))
        for t in plan["planned_rows"]
    }
    rep.check(
        "scale_generate",
        "BR-1 Shape local_mp writes the preset's rows (medium)",
        ok and s_counts == plan["planned_rows"],
    )

    # BR-2: a job store entry does not survive a process (the baseline's store is a dict)
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from sqllocks_spindle.engine.async_job_store import AsyncJobStore, JobRecord\n"
        "import inspect, json, subprocess\n"
        "store = AsyncJobStore(); fields = list(inspect.signature(JobRecord).parameters)\n"
        "attrs = ('path', 'directory', 'root')\n"
        "print(json.dumps({'in_memory': not any(hasattr(store, a) for a in attrs),"
        " 'token_field': 'token' in fields}))"
    )
    from paths import SPINDLE_ROOT

    done = subprocess.run(
        [str(SPINDLE_PY), "-c", code, str(SPINDLE_ROOT)], capture_output=True, text=True
    )
    facts = json.loads(done.stdout.strip().splitlines()[-1]) if done.returncode == 0 else {}
    rep.check(
        "scale_status",
        "BR-2 the baseline's job store is in memory and its record holds the token",
        facts.get("in_memory") is True and facts.get("token_field") is True,
        str(facts or done.stderr[-300:]),
    )
    from tempfile import mkdtemp

    jobs = Path(mkdtemp(dir=work))
    first = Shape(jobs)
    first.call("generate", {"async": True}, domain=DOMAIN, scale="small")
    job_id = first.last["result"]["job_id"]
    first.close()
    second = Shape(jobs)
    ok, state = second.call("job_status", job_id=job_id)
    second.close()
    rep.check(
        "scale_status",
        "BR-2 Shape's job is read by a new bridge process",
        ok and state["status"] == "succeeded",
    )

    # BR-4: a format with no directory
    ok, b = base.call("generate", domain=DOMAIN, scale="small", format="csv")
    s_ok, s = shape.call("generate", domain=DOMAIN, scale="small", format="csv")
    rep.check(
        "generate",
        "BR-4 baseline: success with nothing written; Shape: usage.missing_argument",
        ok and "files" not in b and (not s_ok) and s["code"] == "usage.missing_argument",
    )

    # BR-5: an unknown table in preview
    ok, b = base.call("preview", domain=DOMAIN, tables=["ghost"])
    s_ok, s = shape.call("preview", domain=DOMAIN, tables=["ghost"])
    rep.check(
        "preview",
        "BR-5 baseline: an empty success; Shape: input.invalid_value naming the table",
        ok
        and b["tables"] == {}
        and (not s_ok)
        and s["code"] == "input.invalid_value"
        and "ghost" in s["message"],
    )


def demo(base: Baseline, shape: Shape, rep: Report) -> None:
    ok_b, lb = base.call("demo_list")
    ok_s, ls = shape.call("demo_list")
    assert ok_b and ok_s, (lb, ls)
    keep = ("name", "supported_modes", "domains", "default_rows", "tags")
    pick = [[{k: x[k] for k in keep} for x in r["scenarios"]] for r in (lb, ls)]
    rep.check(
        "demo_list",
        "the scenarios: names, modes, domains, default rows and tags, and the count",
        pick[0] == pick[1] and lb["count"] == ls["count"] == len(pick[0]) > 0,
    )
    args = {"scenario": "retail", "rows": 100, "seed": 42}
    ok_b, rb = base.call("demo_run", **args)
    ok_s, rs = shape.call("demo_run", **args)
    assert ok_b and ok_s, (rb, rs)
    gone = {"session_id"}
    rep.check(
        "demo_run",
        "the result: the same fields, scenario, mode, score and artifact count",
        {k: v for k, v in rb.items() if k not in gone}
        == {k: v for k, v in rs.items() if k not in gone}
        and rs["success"] is True
        and rs["fidelity_score"] is not None,
    )
    ok_b, sb = base.call("demo_status", session_id=rb["session_id"])
    ok_s, ss = shape.call("demo_status", session_id=rs["session_id"])
    assert ok_b and ok_s, (sb, ss)
    names = lambda r: [(a["target"], a["name"], a["row_count"]) for a in r["manifest"]["artifacts"]]  # noqa: E731
    rep.check(
        "demo_status",
        "the manifest: the same keys, scenario, mode, success and artifacts",
        sorted(sb) == sorted(ss)
        and sorted(sb["manifest"]) == sorted(ss["manifest"])
        and all(sb["manifest"][k] == ss["manifest"][k] for k in ("scenario", "mode", "success"))
        and names(sb) == names(ss),
    )
    for command in ("demo_status", "demo_cleanup"):
        ok_b, b = base.call(command, session_id="none")
        ok_s, s = shape.call(command, session_id="none")
        rep.check(
            command,
            "an unknown session is reported (baseline: an error in a success; Shape: "
            "input.invalid_value naming it)",
            ok_b
            and b.get("error") == "session_not_found"
            and (not ok_s)
            and s["code"] == "input.invalid_value"
            and "none" in s["message"],
        )
    ok_b, cb = base.call("demo_cleanup", session_id=rb["session_id"], dry_run=True)
    ok_s, cs = shape.call("demo_cleanup", session_id=rs["session_id"], dry_run=True)
    assert ok_b and ok_s, (cb, cs)
    rep.check(
        "demo_cleanup",
        "a dry run removes nothing and names every artifact (baseline: lists the in-memory ones "
        "as removed; Shape: as skipped, nothing was written)",
        sorted(cb["synthetic"]) == sorted(a["name"] for a in cs["skipped"])
        and cs["removed"] == []
        and cs["dry_run"] is True
        and cs["session_id"] == rs["session_id"],
    )
    ok_s, done = shape.call("demo_cleanup", session_id=rs["session_id"])
    rep.check("demo_cleanup", "a cleanup answers ok", ok_s and done["ok"] is True)


# ─── negative controls ───────────────────────────────────────────────────────────────────────


def negative_controls(got: dict[str, Any], rep: Report) -> None:
    """Each comparison must flag a mutated Shape result."""
    import copy

    def flagged(name: str, problems: list[str]) -> None:
        rep.check("control", f"{name} is flagged", bool(problems))

    b, s = copy.deepcopy(got["describe_3nf"])
    s["tables"]["order"]["columns"][1]["type"] = "binary"
    flagged("describe: a column type changed", compare.describe(b, s))
    b, s = copy.deepcopy(got["describe_3nf"])
    s["relationships"].pop()
    flagged("describe: a relationship dropped", compare.describe(b, s))
    b, s = copy.deepcopy(got["describe_3nf"])
    s["scales"]["small"]["customer"] += 1
    flagged("describe: a preset changed", compare.describe(b, s))
    b, s = copy.deepcopy(got["dry_run"])
    s["planned_rows"]["order"] += 1
    flagged("dry_run: a planned row count changed", compare.dry_run(b, s))
    b, s = copy.deepcopy(got["profile_info"])
    s["ratios"]["address_per_customer"] = 2.0
    flagged("profile_info: a ratio changed", compare.profile_info(b, s, ALIASES, SKIPPED))
    b, s = copy.deepcopy(got["profile_info"])
    s["distributions"]["customer.gender"]["M"] = 0.5
    flagged("profile_info: a weight changed", compare.profile_info(b, s, ALIASES, SKIPPED))
    b, s = copy.deepcopy(got["profile_info"])
    del s["distributions"]["order.status"]
    flagged("profile_info: a distribution dropped", compare.profile_info(b, s, ALIASES, SKIPPED))
    b, s = copy.deepcopy(got["preview"])
    s["tables"]["customer"]["columns"].reverse()
    flagged("preview: the columns reordered", compare.preview(b, s))
    b, s = copy.deepcopy(got["preview"])
    s["tables"]["customer"]["data"][0]["customer_id"] = "x"
    flagged("preview: a value type changed", compare.preview(b, s))
    b, s = copy.deepcopy(got["validate_good"])
    s["table_count"] -= 1
    flagged("validate: a table count changed", compare.validate(b, s))
    if "generate" in got:
        b, s = copy.deepcopy(got["generate"])
        s["tables"]["order"]["rows"] += 5
        flagged("generate: a row count changed", compare.generate_summary(b, s))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--scale",
        default="small",
        choices=("small", "medium"),
        help="the scale of the data comparisons (default small)",
    )
    ap.add_argument(
        "--skip-data", action="store_true", help="metadata and the allow-list only (no T-21 runs)"
    )
    ap.add_argument("--negative-control", action="store_true")
    args = ap.parse_args(argv)
    shutil.rmtree(ROOT, ignore_errors=True)
    ROOT.mkdir(parents=True)
    work = Path(tempfile.mkdtemp(dir=ROOT))
    rep = Report()
    try:
        homes = {}
        for name in ("baseline", "shape"):
            (work / name).mkdir(parents=True, exist_ok=True)
            homes[name] = str(work / name)
        base = Baseline({"HOME": homes["baseline"]})
        shape = Shape(work / "jobs", {"HOME": homes["shape"], "SHAPE_HOME": homes["shape"]})
    except Exception as exc:
        print(f"cannot start a bridge: {exc}", file=sys.stderr)
        return 2
    try:
        print("metadata")
        got = metadata(base, shape, rep)
        got["validate_good"] = validate_cmd(base, shape, rep, work)["good"]
        print("jobs and streams")
        stream_checks(base, shape, rep, work)
        print("baseline defects fixed (allow-list)")
        allow_list(base, shape, rep, work, args.scale)
        print("demo")
        demo(base, shape, rep)
        if not args.skip_data:
            print(f"generated data (scale {args.scale})")
            data_checks(base, shape, rep, args.scale, work, got)
        if args.negative_control:
            print("negative controls")
            negative_controls(got, rep)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print(f"a run could not be completed: {exc}", file=sys.stderr)
        return 2
    finally:
        base.close()
        shape.close()
    failed = [c for c in rep.checks if not c["ok"]]
    covered = sorted({c["command"] for c in rep.checks} & set(PLAN_COMMANDS))
    rep.notes["commands_with_checks"] = covered
    rep.notes["allow_list"] = ALLOW
    rep.notes["not_run"] = {
        "fabric_spark submit/status/cancel": "needs a live Fabric workspace; tested against "
        "recorded interactions in tests/bridge/test_scale.py"
    }
    (ROOT / "report.json").write_text(
        json.dumps({"checks": rep.checks, "notes": rep.notes, "failed": len(failed)}, indent=2)
    )
    print(
        f"\n{len(rep.checks) - len(failed)}/{len(rep.checks)} checks hold; "
        f"commands covered: {len(covered)}/17; report {ROOT / 'report.json'}"
    )
    missing = sorted(set(PLAN_COMMANDS) - set(covered))
    if missing and not args.skip_data:
        print(f"no check for: {', '.join(missing)}", file=sys.stderr)
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
