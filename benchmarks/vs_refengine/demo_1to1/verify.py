"""``shape demo`` against the pinned baseline's ``demo`` command, command by command (P6-12).

    source scripts/env.sh && "$REFENGINE_PY" benchmarks/vs_refengine/demo_1to1/verify.py \\
        [--negative-control]

Runs in the *baseline* venv (it needs pandas and scipy, and imports the T-21 helpers of
``domain_1to1/verify.py``); Shape is driven through its own command line and ``shape_probe.py``
in the Shape venv, each tool with its own scratch home.

What is compared, and how:

* **metadata, exactly** (the baseline's text mapped to Shape's name,
  ``differences.brand_patterns()``): the scenario catalog; the options of every command; a
  record's ``status`` and its Markdown and HTML report (the same record in both tools); the cost
  estimate and the dry-run line; the lines of ``cleanup --dry-run``; the stored connection
  profile; the notebooks (the Markdown cells, the kind of every cell, the scenario, mode and rows
  each runs).
* **the fidelity report, exactly**: the same two datasets of Parquet files (retail at small, two
  baseline seeds, and a copy damaged in known ways) are profiled and compared by both tools; every
  row (table, column, type, null rates, cardinalities, verdict) and the score must be equal. The
  harness refuses to pass vacuously: the damaged copy must give both passes and failures.
* **generated data, T-21**: an inference run's synthetic tables (baseline seeds 42-46, Shape 1042,
  no other set) are compared by T-21 (a)-(d) with the helpers of ``domain_1to1/verify.py``
  (identical tables, columns, order, types and rows; null rates; KS; TVD), and the run's
  fidelity score must lie within the baseline's own spread (granularity: one column). A seeding
  run's output is checked by ``domain_1to1/verify.py --impl shape`` itself, unchanged (T-21
  (a)-(h), retail at small).

Everything that may differ is in ``differences.py``, each entry shown by a probe below (an
observation of the baseline, so an entry that stops being true fails the run); anything else that
differs fails the run. ``--negative-control`` tampers with Shape's output and requires every
tampering to be flagged. Exit codes: 0 every check holds (and every control was flagged), 1 a
check failed, 2 a run could not be produced.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _refpkg  # noqa: E402
from differences import (  # noqa: E402
    ALLOWED,
    DESCRIPTIONS,
    brand,
    describe,
    gone_files_left_alone,
    rows_as_run,
)
from paths import (  # noqa: E402
    BENCH_OUT_DIR,
    REFENGINE_PY,
    REFENGINE_ROOT,
    REFENGINE_VENV,
    SHAPE_VENV,
)

SHAPE_PY = SHAPE_VENV / "bin" / "python"
REFENGINE_CLI = REFENGINE_VENV / "bin" / _refpkg.CONSOLE
SHAPE_CLI = SHAPE_VENV / "bin" / "shape"
DOMAIN_VERIFY = HERE.parent / "domain_1to1" / "verify.py"
REF_SEED, BASELINE_SEEDS, IMPL_SEED = 42, (43, 44, 45, 46), 1042  # T-21: fixed
Problems = list[str]


class RunError(RuntimeError):
    """A run that could not be produced (exit 2 of this script)."""


# ---------------------------------------------------------------------------------------------
# running the tools


def run(
    cmd: list[str], env: dict[str, str] | None = None, cwd: Path | None = None
) -> tuple[int, str, str]:
    done = subprocess.run(  # noqa: S603
        cmd,
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        timeout=1800,
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


class Home:
    """One scratch home for each tool: where its connection profiles and sessions live."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.base = root / "baseline-home"
        self.shape = root / "shape-home"
        for d in (self.base, self.shape):
            d.mkdir(parents=True, exist_ok=True)

    @property
    def base_sessions(self) -> Path:
        return self.base / f".{_refpkg.NAME}" / "sessions"

    @property
    def shape_sessions(self) -> Path:
        return self.shape / "sessions"

    def baseline(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        return run([str(REFENGINE_CLI), "demo", *args], {"HOME": str(self.base)}, cwd)

    def shape_cmd(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        env = {"SHAPE_HOME": str(self.shape), "SHAPE_JOBS_DIR": str(self.shape / "jobs")}
        return run([str(SHAPE_CLI), "demo", *args], env, cwd)

    def put_session(self, record: dict[str, Any]) -> None:
        for sessions in (self.base_sessions, self.shape_sessions):
            sessions.mkdir(parents=True, exist_ok=True)
            (sessions / f"demo-{record['session_id']}.json").write_text(json.dumps(record))


def probe(py: Path, script: str, *args: str) -> Any:
    code, out, err = run([str(py), str(HERE / script), *args])
    if code != 0:
        raise RunError(f"{script} {' '.join(args)} failed: {err[-600:]}")
    return json.loads(out.strip().splitlines()[-1])


def baseline_probe(*args: str) -> Any:
    return probe(REFENGINE_PY, "baseline_probe.py", *args)


def shape_probe(*args: str) -> Any:
    return probe(SHAPE_PY, "shape_probe.py", *args)


def diff(a: Any, b: Any, path: str = "") -> Problems:
    """Where two JSON values differ: one line each."""
    if type(a) is not type(b):
        return [f"{path or '.'}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        out: Problems = []
        for k in sorted(set(a) | set(b)):
            if k not in a:
                out.append(f"{path}.{k}: only in Shape")
            elif k not in b:
                out.append(f"{path}.{k}: only in the baseline")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items != {len(b)} items"]
        return [
            p for i, (x, y) in enumerate(zip(a, b, strict=True)) for p in diff(x, y, f"{path}[{i}]")
        ]
    return [] if a == b else [f"{path or '.'}: {a!r} != {b!r}"]


def session_id(out: str) -> str:
    match = re.search(r"^Session: (\S+)", out, re.MULTILINE)
    if not match:
        raise RunError(f"no session id in: {out[-300:]}")
    return match.group(1)


def load_record(sessions: Path, sid: str) -> dict[str, Any]:
    return json.loads((sessions / f"demo-{sid}.json").read_text())


def record(sid: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "session_id": sid, "scenario": "retail", "mode": "seeding",
        "started_at": "2026-10-03T10:00:00.000001", "finished_at": "2026-10-03T10:00:09.000002",
        "success": True, "error": None, "artifacts": [], "params": {"rows": "1000"},
        "metrics": {"rows_generated": 21750}, "scale_mode": "local", "fabric_run_id": None,
        "workspace_id": None, "notebook_item_id": None,
    }  # fmt: skip
    base.update(over)
    return base


# ---------------------------------------------------------------------------------------------
# metadata


def check_catalog(tmp: Path) -> Problems:
    base = json.loads(brand(json.dumps(baseline_probe("catalog"))))
    for scenario in base:  # description-names-what-runs
        scenario["description"] = describe(scenario["description"])
    mine = shape_probe("catalog")
    return diff(base, mine)


def check_list_cli(tmp: Path) -> Problems:
    home = Home(tmp)
    code, out, _ = home.baseline("list")
    scode, sout, _ = home.shape_cmd("list", "--json")
    if code != 0 or scode != 0:
        return [f"list exited {code} (baseline) and {scode} (Shape)"]
    mine = {s["name"]: s for s in json.loads(sout)["scenarios"]}
    problems: Problems = []
    lines = [x for x in out.splitlines() if x.strip()]
    if [ln.split()[0] for ln in lines] != list(mine):
        problems.append(f"scenario order: {[ln.split()[0] for ln in lines]} != {list(mine)}")
    for line in lines:
        name = line.split()[0]
        s = mine[name]
        want = f"{name:20} {', '.join(s['supported_modes']):30} " + s["description"][:60]
        if describe(brand(line), width=60).rstrip() != want.rstrip():
            problems.append(f"{name}: {line!r} != {want!r}")
    return problems


def options(text: str) -> tuple[set[str], dict[str, set[str]]]:
    """The long options in a ``--help`` text, and the choices of those that list them (click
    writes ``--mode [a|b]``, argparse ``--mode {a,b}``)."""
    flags = set(re.findall(r"(?<![\w-])(--[a-z][a-z-]*)", text))
    choices: dict[str, set[str]] = {}
    for match in re.finditer(r"(--[a-z-]+)[^\n\[{]*(?:\[([\w|-]+)\]|\{([\w,-]+)\})", text):
        listed = match.group(2) or match.group(3)
        choices[match.group(1)] = set(re.split(r"[|,]", listed))
    return flags, choices


def check_options(tmp: Path) -> Problems:
    """Every option and choice of the baseline's commands is in Shape's."""
    home = Home(tmp)
    problems: Problems = []
    for command in ("init", "list", "run", "preflight", "cleanup", "status", "notebook", "report"):
        _, b, _ = home.baseline(command, "--help")
        code, s, _ = home.shape_cmd(command, "--help")
        if code != 0:
            problems.append(f"shape demo {command} --help exited {code}")
            continue
        bf, bc = options(b)
        sf, sc = options(s)
        for flag in sorted(bf - sf - {"--help"}):
            problems.append(f"{command}: the baseline's {flag} is missing in Shape")
        for flag, values in bc.items():
            mine = sc.get(flag, set())
            if not values <= mine:
                problems.append(f"{command} {flag}: baseline choices {values} not in {mine}")
    return problems


# ---------------------------------------------------------------------------------------------
# the fidelity report


def datasets(tmp: Path) -> tuple[Path, Path, Path]:
    a, b, b2 = tmp / "A", tmp / "B", tmp / "B2"
    baseline_probe("dataset", str(a), "43")
    baseline_probe("dataset", str(b), "44")
    baseline_probe("dataset", str(b2), "44", "--perturb")
    return a, b, b2


def compare_fidelity(base: dict[str, Any], mine: dict[str, Any]) -> Problems:
    return diff(base["rows"], mine["rows"], "rows") + diff(base["score"], mine["score"], "score")


def check_fidelity(tmp: Path) -> Problems:
    a, b, b2 = datasets(tmp)
    problems: Problems = []
    for label, other in (("same distribution", b), ("damaged copy", b2)):
        base = baseline_probe("fidelity", str(a), str(other))
        mine = shape_probe("fidelity", str(a), str(other))
        problems += [f"{label}: {p}" for p in compare_fidelity(base, mine)]
        verdicts = [r["pass"] for r in base["rows"]]
        if label == "damaged copy" and not (any(verdicts) and not all(verdicts)):
            problems.append("the damaged copy gives no mix of passes and failures: vacuous")
    return problems


# ---------------------------------------------------------------------------------------------
# generated data (T-21)


def load_tables(folder: Path) -> dict[str, Any]:
    import pandas as pd

    names = json.loads((folder / "_order.json").read_text())
    return {n: pd.read_parquet(folder / f"{n}.parquet") for n in names}


def domain_verify() -> Any:
    spec = importlib.util.spec_from_file_location("domain_verify", DOMAIN_VERIFY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["domain_verify"] = module
    spec.loader.exec_module(module)
    return module


def norm_type(dtype: Any) -> str:
    return str(dtype).replace("large_string", "string")


def signature(value: str) -> str:
    """The shape of a text value: runs of letters, of digits and of spaces collapsed."""
    return re.sub(r"\s+", " ", re.sub(r"\d+", "9", re.sub(r"[^\W\d_]+", "a", value)))


def vocabulary_problems(ref: Any, mine: Any, others: list[Any], overlap: float) -> list[str]:
    """T-21 (d)/(e) without the domain's pools: values drawn from a pool by a few hundred rows
    do not repeat between two runs, in the baseline either. The share of Shape's values outside
    the baseline's vocabulary may be 1.5 times the largest share of another baseline seed's
    values outside it (the standard's own 1.5 x drift), and every value must have the shape of a
    baseline value."""
    vocab = set(ref.dropna().astype(str))
    drift = max(
        (1.0 - float(o.dropna().astype(str).isin(vocab).mean()) for o in others if o.notna().any()),
        default=0.0,
    )
    problems = []
    if 1.0 - overlap > max(0.001, 1.5 * drift):
        problems.append(f"vocab (outside {1 - overlap:.3f}, allowed {max(0.001, 1.5 * drift):.3f})")
    shapes = {signature(v) for v in vocab}
    for o in others:
        shapes |= {signature(v) for v in o.dropna().astype(str)}
    outside = {v for v in mine.dropna().astype(str) if signature(v) not in shapes}
    if len(outside) > 0.001 * max(len(mine), 1):
        problems.append(f"value shape ({len(outside)} values of an unseen shape)")
    return problems


NOTES: list[str] = []  # facts worth reading that are not failures; printed at the end


def compare_tables(
    ref: dict[str, Any],
    seeds: list[dict[str, Any]],
    mine: dict[str, Any],
    skip: frozenset[tuple[str, str]] = frozenset(),
) -> Problems:
    """T-21 (a)-(d) between the baseline's reference run, its other seeds and Shape's run.

    A column's strategy is chosen from the profile of a small sample, and a borderline column
    (a text column whose distinct share is near the enum threshold) is an enum on one seed and a
    name generator on the next, in the baseline as in Shape. So each column is compared with the
    baseline runs that chose the same strategy as Shape (all five when the choice is stable); a
    strategy the baseline never chose is a failure."""
    dv = domain_verify()
    runs = [ref, *seeds]
    problems: Problems = []
    if set(ref["tables"]) != set(mine["tables"]):
        return [f"(a) tables: {sorted(ref['tables'])} != {sorted(mine['tables'])}"]
    for name in ref["tables"]:
        sp, im = ref["tables"][name], mine["tables"][name]
        if list(sp.columns) != list(im.columns):
            problems.append(f"(a) {name}: columns {list(sp.columns)} != {list(im.columns)}")
            continue
        if len(sp) != len(im):
            problems.append(f"(a) {name}: {len(sp)} rows != {len(im)}")
            continue
        for col in sp.columns:
            if (name, col) in skip:
                continue
            if dv.kind(sp[col]) != dv.kind(im[col]):
                problems.append(f"(a) {name}.{col}: type {sp[col].dtype} != {im[col].dtype}")
                continue
            decisions = [r["info"]["strategies"][name][col] for r in runs]
            chosen = mine["info"]["strategies"][name][col]
            if chosen not in decisions:
                problems.append(
                    f"(learn) {name}.{col}: Shape chose {chosen}; the baseline chose "
                    f"{sorted(set(decisions))}"
                )
                continue
            same = [r for r, d in zip(runs, decisions, strict=True) if d == chosen]
            if len(set(decisions)) > 1:
                NOTES.append(
                    f"{name}.{col}: borderline strategy ({', '.join(sorted(set(decisions)))}); "
                    f"{len(same)} of {len(runs)} baseline runs chose Shape's ({chosen})"
                )
            reference, others = (
                same[0]["tables"][name][col],
                [r["tables"][name][col] for r in same[1:]],
            )
            problems += [
                f"(b-d) {name}.{col}: {p}" for p in column_problems(dv, reference, im[col], others)
            ]
    return problems


def note_strategy_differences(runs: list[dict[str, Any]], mine: dict[str, Any]) -> None:
    """Columns whose learned strategy Shape's run chose and no baseline run did. The sample each
    tool profiles is its own domain's output, so this follows from the domains, not from the
    demo; the same file gives the same strategies (``check_inference_file``)."""
    for name, columns in mine["info"]["strategies"].items():
        for col, chosen in columns.items():
            seen = {r["info"]["strategies"].get(name, {}).get(col) for r in runs}
            if chosen not in seen:
                NOTES.append(
                    f"{name}.{col}: learned {chosen} from Shape's domain sample; "
                    f"the baseline learned {sorted(x for x in seen if x)} from its own"
                )


def column_problems(dv: Any, sp: Any, im: Any, others: list[Any]) -> list[str]:
    """T-21 (b)-(d) for one column against a reference and the other baseline runs of the same
    strategy (none: only the null rate and the shape of the values can be checked)."""
    nan = float("nan")
    if others:
        B = dv.merge_baselines([dv.baseline_distances(sp, o) for o in others])
    else:
        B = {"null": 0.0, "ks": nan, "tvd": 0.0, "dratio_dev": 0.0}
    r = dv.compare_column(sp, im, B, None, None)
    failed = [k for k, ok in r["checks"].items() if not ok]
    if r["kind"] != "categorical":
        return failed if others else [k for k in failed if k not in ("ks",)]
    if r["distinct"]["refengine"] > 1000:
        # a text column with thousands of distinct values has no vocabulary to overlap (its
        # values are made up per run): its distinct count is what is compared
        failed = [k for k in failed if k != "vocab"]
    elif "vocab" in failed:
        failed = [k for k in failed if k != "vocab"] + vocabulary_problems(
            sp, im, others, r["vocab_overlap"]
        )
    if not others:
        failed = [k for k in failed if k not in ("tvd", "distinct_ratio")]
    return failed


def inference_runs(
    tmp: Path, scenario: str, rows: int, input_file: Path | None = None
) -> tuple[dict, list[dict], dict]:
    """The baseline at seed 42 (reference) and 43-46, and Shape at 1042: results and tables."""
    extra = [str(input_file)] if input_file else []
    base: dict[int, dict[str, Any]] = {}
    for seed in (REF_SEED, *BASELINE_SEEDS):
        out = tmp / f"baseline-{seed}"
        info = baseline_probe("inference", scenario, str(rows), str(seed), str(out), *extra)
        base[seed] = {"info": info, "tables": load_tables(out)}
    out = tmp / f"shape-{IMPL_SEED}"
    info = shape_probe("inference", scenario, str(rows), str(IMPL_SEED), str(out), *extra)
    mine = {"info": info, "tables": load_tables(out)}
    return base[REF_SEED], [base[s] for s in BASELINE_SEEDS], mine


def compare_inference(
    ref: dict,
    seeds: list[dict],
    mine: dict,
    *,
    data: bool = True,
    skip: frozenset[tuple[str, str]] = frozenset(),
) -> Problems:
    problems: Problems = []
    bi, mi = ref["info"], mine["info"]
    # exact: the tables, their columns, rows and the record's metrics and artifacts
    if bi["tables"].keys() != mi["tables"].keys():
        problems.append(f"tables: {sorted(bi['tables'])} != {sorted(mi['tables'])}")
    problems += diff(bi["tables"], {k: mi["tables"][k] for k in bi["tables"] if k in mi["tables"]})
    if set(bi["metrics"]) != set(mi["metrics"]):
        problems.append(f"metric keys: {sorted(bi['metrics'])} != {sorted(mi['metrics'])}")
    if bi["metrics"].get("tables_profiled") != mi["metrics"].get("tables_profiled"):
        problems.append("tables_profiled differs")
    want = {a["name"]: (a["target"], a["row_count"]) for a in bi["artifacts"]}
    got = {a["name"]: (a["target"], a["row_count"]) for a in mi["artifacts"]}
    problems += diff(want, got, "artifacts")
    # the score: within the baseline's own spread, to the granularity of one column
    scores = [s["info"]["score"] for s in (ref, *seeds)]
    columns = sum(len(t["columns"]) for t in bi["tables"].values())
    tol = max(max(scores) - min(scores), 1.0 / columns)
    if not (min(scores) - tol <= mi["score"] <= max(scores) + tol):
        problems.append(
            f"fidelity score {mi['score']:.4f} outside "
            f"{min(scores):.4f}..{max(scores):.4f} ± {tol:.4f}"
        )
    if data:
        problems += compare_tables(ref, seeds, mine, skip)
    else:
        note_strategy_differences([ref, *seeds], mine)
    return problems


def check_inference(tmp: Path) -> Problems:
    """Domain defaults: the tables, columns, rows, record and score. The sample each tool profiles
    is its own domain's output, so the strategies learned from it can differ on a borderline
    column (a note); the values are compared where the input is the same (the next check)."""
    ref, seeds, mine = inference_runs(tmp, "retail", 1000)
    return compare_inference(ref, seeds, mine, data=False)


INPUT_TABLES = ("customer", "order", "order_line")


def learn_differences(
    table: str, source: Any, base: dict[str, str], mine: dict[str, str]
) -> set[str]:
    """The columns where Shape's learned strategy differs from the baseline's by one of the rules
    ``shape.generation.learn.DIFFERENCES`` names (P4-08), and where the rule's condition holds:
    ``truncated_enum``: a numeric column with more distinct values than the profile lists (its
    top 500) is generated from its distribution, not drawn from those 500 values."""
    found = set()
    for col, theirs in base.items():
        ours = mine.get(col)
        if ours == theirs:
            continue
        series = source[col]
        numeric = series.dtype.kind in "iuf"
        if (
            (theirs, ours) == ("weighted_enum", "distribution")
            and numeric
            and series.nunique() > 500
        ):
            found.add(col)
    return found


def check_inference_file(tmp: Path) -> Problems:
    """The same file in, the same learned schema out (but for the rule P4-08 names), and T-21
    (a)-(d) on the generated values of every column that has the same strategy."""
    a = tmp / "A"
    baseline_probe("dataset", str(a), "43")
    problems: Problems = []
    for table in INPUT_TABLES:
        ref, seeds, mine = inference_runs(tmp / table, "retail", 1000, a / f"{table}.parquet")
        strategies = [r["info"]["strategies"] for r in (ref, *seeds)]
        if any(x != strategies[0] for x in strategies):
            problems.append(f"{table}: the baseline's strategies depend on the seed")
        source = load_tables(a)[table]
        known = learn_differences(
            table, source, strategies[0][table], mine["info"]["strategies"][table]
        )
        for col in sorted(known):
            NOTES.append(
                f"{table}.{col}: truncated_enum (learn.DIFFERENCES): the baseline draws from the "
                f"top 500 of {source[col].nunique()} values, Shape from the distribution"
            )
        expected = copy.deepcopy(strategies[0])
        for col in known:
            expected[table][col] = mine["info"]["strategies"][table][col]
        problems += [
            f"{table}: {p}" for p in diff(expected, mine["info"]["strategies"], "strategies")
        ]
        skip = frozenset((table, c) for c in known)
        for r in (ref, *seeds):
            r["info"]["strategies"] = {
                t: {
                    c: (mine["info"]["strategies"][t][c] if (t, c) in skip else s_)
                    for c, s_ in cols.items()
                }
                for t, cols in r["info"]["strategies"].items()
            }
        problems += [f"{table}: {p}" for p in compare_inference(ref, seeds, mine, skip=skip)]
    return problems


def flatten_demo_output(landing: Path, session: str, run_dir: Path, order: list[str]) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    shutil.rmtree(run_dir, ignore_errors=True)
    run_dir.mkdir(parents=True)
    rows: dict[str, int] = {}
    for table in order:
        parts = sorted((landing / session / table).glob("part-*.parquet"))
        if not parts:
            raise RunError(f"no part files for {table} in {landing / session}")
        t = pa.concat_tables([pq.read_table(p) for p in parts])
        pq.write_table(t, run_dir / f"{table}.parquet", compression="snappy")
        rows[table] = t.num_rows
    (run_dir / "_SUCCESS").write_text(
        json.dumps(
            {"impl": "shape", "domain": "retail", "scale": "small", "seed": IMPL_SEED, "rows": rows}
        )
    )


def seeding_output(tmp: Path) -> tuple[Path, list[str], dict[str, int]]:
    """A seeding run through ``shape demo`` into a local folder, flattened for the domain
    verifier: returns the root it reads, the table order and the rows of each table."""
    home = Home(tmp)
    landing = tmp / "landing"
    code, _, err = home.shape_cmd("init", "--name", "local", "--local-path", str(landing))
    if code != 0:
        raise RunError(f"demo init failed: {err}")
    code, out, err = home.shape_cmd(
        "run", "retail", "--mode", "seeding", "--connection", "local", "--rows", "1000",
        "--seed", str(IMPL_SEED),
    )  # fmt: skip
    if code != 0:
        raise RunError(f"demo run failed: {err[-600:]}")
    sid = session_id(out)
    code, plan, err = run(
        [str(SHAPE_CLI), "generate", "retail", "--scale", "small", "--dry-run", "--json"]
    )
    if code != 0:
        raise RunError(f"shape generate --dry-run failed: {err[-400:]}")
    order = [t for level in json.loads(plan)["levels"] for t in level]
    root = tmp / "t21"
    flatten_demo_output(
        landing, sid, root / "shape" / "retail" / "small" / f"seed{IMPL_SEED}", order
    )
    rec = load_record(home.shape_sessions, sid)
    return root, order, {a["name"]: a["row_count"] for a in rec["artifacts"]}


def run_domain_verify(root: Path, label: str) -> int:
    shared = BENCH_OUT_DIR / "refengine"
    shared.mkdir(parents=True, exist_ok=True)
    link = root / "refengine"
    if not link.exists():
        link.symlink_to(shared, target_is_directory=True)
    cmd = [
        str(REFENGINE_PY), str(DOMAIN_VERIFY), "--domain", "retail", "--scale", "small",
        "--impl", "shape", "--out", str(root / f"{label}.json"),
    ]  # fmt: skip
    code, out, err = run(cmd, {"BENCH_OUT_DIR": str(root)})
    (root / f"{label}.log").write_text(out + "\n" + err)
    return code


def check_seeding_data(tmp: Path) -> Problems:
    root, order, rows = seeding_output(tmp)
    problems: Problems = []
    code, plan, _ = run([str(SHAPE_CLI), "presets", "retail", "--json"])
    preset = {t: int(n) for t, n in json.loads(plan)["small"].items()}
    if rows != preset:
        problems.append(f"the record's rows {rows} != the preset {preset}")
    code = run_domain_verify(root, "t21")
    if code == 2:
        raise RunError(f"the domain verifier could not run; see {root / 't21.log'}")
    if code != 0:
        problems.append(f"domain_1to1/verify.py --impl shape exited {code} (T-21)")
    return problems


# ---------------------------------------------------------------------------------------------
# records: status, report, cleanup, init, estimate


def check_status_report(tmp: Path) -> Problems:
    home = Home(tmp)
    artifacts = [
        {"target": "file", "name": "customer", "row_count": 1000, "detail": "/x/customer"},
        {"target": "warehouse", "name": "order", "row_count": 5000, "detail": "dbo.order"},
        {"target": "eventhouse", "name": "order_line", "row_count": 12500, "detail": ""},
    ]
    home.put_session(record("aaaa1111", artifacts=artifacts))
    home.put_session(
        record("bbbb2222", success=False, error="the load failed", finished_at=None, metrics={})
    )
    home.put_session(
        record("cccc3333", artifacts=[], metrics={"fidelity_score": 0.9831, "tables_profiled": 9})
    )
    problems: Problems = []
    for sid in ("aaaa1111", "bbbb2222", "cccc3333"):
        bc, b, _ = home.baseline("status", sid)
        sc, s, _ = home.shape_cmd("status", sid)
        if (bc, sc) != (0, 0):
            problems.append(f"status {sid}: exit {bc} / {sc}")
        elif brand(b) != s:
            problems += [f"status {sid}: {p}" for p in diff(brand(b).splitlines(), s.splitlines())]
        for fmt in ("md", "html"):
            bc, b, _ = home.baseline("report", sid, "--format", fmt)
            sc, s, _ = home.shape_cmd("report", sid, "--format", fmt)
            if (bc, sc) != (0, 0):
                problems.append(f"report {sid} {fmt}: exit {bc} / {sc}")
                continue
            want = brand(b)
            got = s.replace('<meta charset="utf-8">', "")
            if want.strip() != got.strip():
                problems += [
                    f"report {sid} {fmt}: {p}"
                    for p in diff(want.strip().splitlines(), got.strip().splitlines())[:5]
                ]
    return problems


def check_report(tmp: Path) -> Problems:  # the entry's probe name: html-declares-its-charset
    home = Home(tmp)
    home.put_session(record("aaaa1111"))
    _, b, _ = home.baseline("report", "aaaa1111", "--format", "html")
    _, s, _ = home.shape_cmd("report", "aaaa1111", "--format", "html")
    problems: Problems = []
    if "charset" in b.lower():
        problems.append("the baseline's page declares a charset: the allow-list entry is stale")
    if '<meta charset="utf-8">' not in s:
        problems.append("Shape's page does not declare UTF-8")
    return problems


def baseline_written_rows(rows: int) -> int:
    """The rows the baseline's local seeding run of retail writes for ``--rows rows``, counted
    where its tables reach the sinks (its record states ``--rows`` instead: rows-are-what-runs)."""
    return sum(baseline_probe("written", str(rows), "43")["tables"].values())


def baseline_as_run(text: str, asked: int, written: int, dry_run: bool) -> str:
    """The baseline's estimate output with the rows its run writes (rows-are-what-runs)."""
    match = re.search(r"^  Targets: (.*)$", text, re.MULTILINE)
    if not match:
        raise ValueError("the baseline's estimate names no targets")
    targets = ",".join(match.group(1).split(", "))
    at_asked = baseline_probe("estimate", str(asked), targets)
    at_written = baseline_probe("estimate", str(written), targets)
    return rows_as_run(
        text,
        scenario="retail",
        asked=asked,
        written=written,
        scale=at_asked["scale"],
        asked_block=at_asked["text"],
        written_block=at_written["text"],
        dry_run=dry_run,
    )


def estimate_lines(text: str) -> list[str]:
    """The lines of a ``run --estimate`` or ``--dry-run`` that both tools print."""
    return [
        x
        for x in text.splitlines()
        if x.strip() and not x.startswith(("Session", "===", "  >>", "     "))
    ]


def check_estimate(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    written = baseline_written_rows(1000)
    cases = (
        ("none", [], []),
        (
            "all four targets",
            [
                "--workspace-id",
                "w",
                "--lakehouse-id",
                "l",
                "--warehouse-conn",
                "Driver={x};Server=s",
                "--eventhouse-uri",
                "https://e.example.test",
                "--sql-db-conn",
                "Driver={y};Server=t",
            ],  # fmt: skip
            [
                "--warehouse-staging-path",
                "onelake://w/l/Files/stage",
                "--eventhouse-database",
                "db",
            ],  # fmt: skip
        ),
    )
    for label, shared, extra in cases:
        conn: list[str] = []
        if shared:
            for h, args in (
                (home.baseline, ["init", "--name", "p", *shared, "--auth", "cli"]),
                (home.shape_cmd, ["init", "--name", "p", *shared, *extra, "--auth", "cli"]),
            ):
                code, _, err = h(*args)
                if code != 0:
                    raise RunError(f"init failed: {err[-300:]}")
            conn = ["--connection", "p"]
        for flag in ("--estimate", "--dry-run"):
            args = ["run", "retail", "--mode", "seeding", "--rows", "1000", *conn, flag]
            bc, b, _ = home.baseline(*args)
            sc, s, _ = home.shape_cmd(*args)
            if (bc, sc) != (0, 0):
                problems.append(f"{label} {flag}: exit {bc} / {sc}")
                continue
            try:
                b = baseline_as_run(b, 1000, written, flag == "--dry-run")
            except ValueError as exc:
                problems.append(f"{label} {flag}: {exc}")
                continue
            problems += [
                f"{label} {flag}: {p}" for p in diff(estimate_lines(brand(b)), estimate_lines(s))
            ]
    return problems


CLEANUP_ARTIFACTS = (
    ("file", "a"),
    ("warehouse", "b"),
    ("sql_db", "c"),
    ("lakehouse", "d"),
    ("eventhouse", "e"),
    ("warehouse", "f"),
)


def cleanup_dry_runs(tmp: Path) -> tuple[list[str], list[str]]:
    """Both tools' ``cleanup --dry-run`` lines for the same record, run in ``tmp`` (a ``file``
    artifact names a path there), with the baseline's lines for the local files that are not
    there mapped to Shape's (dry-run-judges-files)."""
    home = Home(tmp)
    artifacts = [
        {"target": t, "name": n, "row_count": 3, "detail": ""} for t, n in CLEANUP_ARTIFACTS
    ]
    home.put_session(record("dddd4444", artifacts=artifacts))
    bc, b, _ = home.baseline("cleanup", "dddd4444", "--dry-run", cwd=tmp)
    sc, s, _ = home.shape_cmd("cleanup", "dddd4444", "--dry-run", cwd=tmp)
    if (bc, sc) != (0, 0):
        raise RunError(f"cleanup --dry-run: exit {bc} / {sc}")
    gone = [n for t, n in CLEANUP_ARTIFACTS if t == "file" and not (tmp / n).exists()]
    return gone_files_left_alone(b.splitlines(), gone), s.splitlines()


def check_cleanup_dry_run(tmp: Path) -> Problems:
    try:
        base, mine = cleanup_dry_runs(tmp)
    except ValueError as exc:
        return [str(exc)]
    return diff(base, mine)


def check_init(tmp: Path) -> Problems:
    home = Home(tmp)
    args = [
        "init", "--name", "dev", "--workspace-id", "ws-1",
        "--warehouse-conn", "Driver={x};Server=s;Database=d",
        "--eventhouse-uri", "https://k.example.test", "--sql-db-conn", "Driver={y};Server=t",
        "--lakehouse-id", "lh-1", "--auth", "msi",
    ]  # fmt: skip
    bc, bout, _ = home.baseline(*args)
    sc, sout, _ = home.shape_cmd(*args)
    if (bc, sc) != (0, 0):
        return [f"init: exit {bc} / {sc}"]
    base = json.loads((home.base / f".{_refpkg.NAME}" / "connections.json").read_text())["dev"]
    mine = json.loads((home.shape / "connections.json").read_text())["dev"]
    problems = [
        f"profile field {k}: {v!r} != {mine.get(k)!r}" for k, v in base.items() if mine.get(k) != v
    ]
    problems += (
        diff(brand(bout).replace(f"{_refpkg.NAME} demo run", "shape demo run"), sout)
        if brand(bout) != sout
        else []
    )
    return problems


def check_notebook(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    cases = [
        ("retail", "inference"), ("retail", "streaming"), ("retail", "seeding"),
        ("adventureworks", "inference"), ("adventureworks", "seeding"),
        ("healthcare", "inference"), ("enterprise", "seeding"),
    ]  # fmt: skip
    for scenario, mode in cases:
        b_path, s_path = tmp / f"b-{scenario}-{mode}.ipynb", tmp / f"s-{scenario}-{mode}.ipynb"
        bc, _, _ = home.baseline("notebook", scenario, "--mode", mode, "--output", str(b_path))
        sc, _, _ = home.shape_cmd("notebook", scenario, "--mode", mode, "--output", str(s_path))
        if (bc, sc) != (0, 0):
            problems.append(f"{scenario}/{mode}: exit {bc} / {sc}")
            continue
        for p in notebook_differences(
            json.loads(b_path.read_text()), json.loads(s_path.read_text())
        ):
            problems.append(f"{scenario}/{mode}: {p}")
    return problems


def run_facts(nb: dict[str, Any]) -> dict[str, str]:
    """The scenario, mode and rows a notebook runs, and the domain when it names one."""
    code = "\n".join("".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code")
    facts = {}
    for key in ("scenario", "mode", "rows", "domain"):
        match = re.search(rf"\b{key}=([^,\n]+)", code)
        if match:
            facts[key] = match.group(1).strip().strip("'\"").replace("_", "")
    return facts


def notebook_differences(base: dict[str, Any], mine: dict[str, Any]) -> Problems:
    problems: Problems = []
    if (base["nbformat"], base["nbformat_minor"]) != (mine["nbformat"], mine["nbformat_minor"]):
        problems.append("nbformat differs")
    kinds_b = [c["cell_type"] for c in base["cells"]]
    kinds_s = [c["cell_type"] for c in mine["cells"]]

    # the baseline has the same cells with one more code cell (its imports and the seed); Shape's
    # is a single import cell: compare the markdown cells and the order of kinds without code runs
    def squash(kinds: list[str]) -> list[str]:
        return [k for i, k in enumerate(kinds) if i == 0 or k != "code" or kinds[i - 1] != "code"]

    if squash(kinds_b) != squash(kinds_s):
        problems.append(f"cell kinds {squash(kinds_b)} != {squash(kinds_s)}")
    md_b = ["".join(c["source"]) for c in base["cells"] if c["cell_type"] == "markdown"]
    md_s = ["".join(c["source"]) for c in mine["cells"] if c["cell_type"] == "markdown"]
    problems += diff(
        [describe(brand(x)).strip() for x in md_b], [x.strip() for x in md_s], "markdown"
    )
    fb, fs = run_facts(base), run_facts(mine)
    for key in ("scenario", "mode", "rows"):
        if fb.get(key) != fs.get(key):
            problems.append(f"{key}: {fb.get(key)!r} != {fs.get(key)!r}")
    if "domain" in fs and fb.get("domain") != fs["domain"]:
        problems.append(f"domain: {fb.get('domain')!r} != {fs['domain']!r}")
    if _refpkg.NAME.lower() in json.dumps(mine).lower():
        problems.append("the baseline's name is in Shape's notebook")
    return problems


# ---------------------------------------------------------------------------------------------
# probes of the allowed differences (observations of the baseline)


def read(path: str) -> str:
    return (REFENGINE_ROOT / _refpkg.PACKAGE / path).read_text()


def probe_scenario_domain(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    retail = {
        "customer",
        "product",
        "order",
        "order_line",
        "address",
        "store",
        "promotion",
        "return",
        "product_category",
    }
    for scenario in ("healthcare", "enterprise"):
        code, out, err = home.baseline(
            "run", scenario, "--mode", "seeding", "--rows", "1000", "--seed", "43"
        )
        if code != 0:
            problems.append(f"baseline {scenario}: exit {code}: {err[-200:]}")
            continue
        names = {a["name"] for a in load_record(home.base_sessions, session_id(out))["artifacts"]}
        if not names or not names <= retail:
            problems.append(
                f"the baseline's {scenario} run no longer generates retail tables: {sorted(names)}"
            )
    # Shape runs the scenario's own domain: healthcare tables, none of retail's
    code, out, err = home.shape_cmd("run", "healthcare", "--mode", "seeding", "--rows", "1000")
    if code != 0:
        problems.append(f"Shape's healthcare run: exit {code}, {err[-200:]!r}")
    else:
        names = {a["name"] for a in load_record(home.shape_sessions, session_id(out))["artifacts"]}
        if "patient" not in names or names & retail:
            problems.append(f"Shape's healthcare run did not generate healthcare: {sorted(names)}")
    # ... and fails, naming it, when the domain asked for is not installed
    code, out, err = home.shape_cmd(
        "run", "retail", "--mode", "seeding", "--rows", "1000", "--domain", "no-such-domain"
    )
    if code != 1 or "no domain named 'no-such-domain'" not in out + err:
        problems.append(f"Shape's run of a missing domain: exit {code}, {err[-200:]!r}")
    return problems


def probe_row_counts(tmp: Path) -> Problems:
    home = Home(tmp)
    code, out, _ = home.baseline(
        "run", "retail", "--mode", "seeding", "--rows", "1000", "--seed", "43"
    )
    rows = {a["row_count"] for a in load_record(home.base_sessions, session_id(out))["artifacts"]}
    problems: Problems = []
    if code != 0 or len(rows) != 1:
        problems.append(f"the baseline's artifacts no longer all carry one row count: {rows}")
    code, out, _ = home.shape_cmd("run", "retail", "--mode", "seeding", "--rows", "1000")
    mine = {
        a["name"]: a["row_count"]
        for a in load_record(home.shape_sessions, session_id(out))["artifacts"]
    }
    if len(set(mine.values())) < 5 or mine.get("customer") != 1000:
        problems.append(f"Shape's artifacts do not carry each table's rows: {mine}")
    return problems


def probe_dry_run(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    code, out, _ = home.baseline("run", "retail", "--rows", "1000", "--seed", "43", "--dry-run")
    if code != 0 or not load_record(home.base_sessions, session_id(out))["artifacts"]:
        problems.append("the baseline's inference --dry-run no longer runs for real")
    code, out, _ = home.shape_cmd("run", "retail", "--rows", "1000", "--dry-run")
    if (
        code != 0
        or "[dry-run]" not in out
        or load_record(home.shape_sessions, session_id(out))["artifacts"]
    ):
        problems.append("Shape's inference --dry-run generated something")
    return problems


def probe_estimate_rows(tmp: Path) -> Problems:
    """rows-are-what-runs: the baseline's estimate and record state --rows while its run writes
    the preset's rows; Shape's estimate states the rows its run writes."""
    home = Home(tmp)
    problems: Problems = []
    seen = baseline_probe("written", "1000", "43")
    written = sum(seen["tables"].values())
    _, out, _ = home.baseline("run", "retail", "--mode", "seeding", "--rows", "1000", "--estimate")
    if written == 1000 or seen["rows_generated"] != 1000 or "(1,000 rows)" not in out:
        problems.append(
            f"the baseline no longer states --rows apart from what it writes ({written:,} rows "
            f"written, {seen['rows_generated']:,} recorded)"
        )
    code, out, err = home.shape_cmd("run", "retail", "--mode", "seeding", "--rows", "1000")
    if code != 0:
        return [*problems, f"Shape's seeding run: exit {code}, {err[-200:]!r}"]
    mine = load_record(home.shape_sessions, session_id(out))["metrics"]["rows_generated"]
    _, est, _ = home.shape_cmd("run", "retail", "--mode", "seeding", "--rows", "1000", "--estimate")
    if mine != written or f"small scale preset: {mine:,} rows" not in est:
        problems.append(f"Shape's estimate does not state the {written:,} rows the runs write")
    return problems


def probe_descriptions(tmp: Path) -> Problems:
    """description-names-what-runs: the tables the baseline's adventureworks description names
    are not the tables its run generates; Shape's run generates what its description names."""
    home = Home(tmp)
    problems: Problems = []
    catalog = {s["name"]: s["description"] for s in baseline_probe("catalog")}
    shape_catalog = {s["name"]: s["description"] for s in shape_probe("catalog")}
    for scenario, (old, new) in DESCRIPTIONS.items():
        if catalog.get(scenario) != old or shape_catalog.get(scenario) != new:
            problems.append(f"{scenario}: a description is no longer the one named")
            continue
        named = set(re.findall(r"\b(?:Dim|Fact)[A-Za-z]+", old))
        code, out, err = home.baseline(
            "run", scenario, "--mode", "seeding", "--rows", "1000", "--seed", "43"
        )
        if code != 0:
            problems.append(f"baseline {scenario}: exit {code}: {err[-200:]}")
            continue
        names = {a["name"] for a in load_record(home.base_sessions, session_id(out))["artifacts"]}
        if not named or named & names:
            problems.append(f"the baseline's {scenario} run generates the tables it names: {names}")
        code, out, err = home.shape_cmd("run", scenario, "--mode", "seeding", "--rows", "1000")
        if code != 0:
            problems.append(f"Shape's {scenario} run: exit {code}, {err[-200:]!r}")
            continue
        mine = {a["name"] for a in load_record(home.shape_sessions, session_id(out))["artifacts"]}
        if not {"customer", "product", "order", "order_line"} <= mine:
            problems.append(f"Shape's {scenario} run does not generate what it names: {mine}")
    return problems


def probe_cleanup_honesty(tmp: Path) -> Problems:
    home = Home(tmp)
    artifacts = [
        {"target": "lakehouse", "name": "x", "row_count": 1, "detail": "onelake://w/l/Files/x"},
        {"target": "eventhouse", "name": "y", "row_count": 1, "detail": ""},
        {"target": "sqldatabase", "name": "z", "row_count": 1, "detail": ""},
    ]
    home.put_session(record("eeee5555", artifacts=artifacts))
    problems: Problems = []
    bc, b, berr = home.baseline("cleanup", "eeee5555")
    if not (
        bc == 0
        and "Removed: lakehouse/x" in b
        and "Removed: eventhouse/y" in b
        and "not implemented" in berr
    ):
        problems.append("the baseline no longer reports an unremoved artifact as removed")
    if (
        "Removed: sqldatabase/z" in b
        or "sqldatabase" not in read("demo/modes/seeding.py") + "sqldatabase"
    ):
        problems.append("the baseline now removes the sqldatabase label")
    if '.replace("Sink", "").lower()' not in read("demo/modes/seeding.py"):
        problems.append("the baseline no longer labels artifacts with its sink class name")
    sc, s, serr = home.shape_cmd("cleanup", "eeee5555")
    if (
        sc != 1
        or "Removed:" in s
        or "FAILED: lakehouse/x" not in serr
        or "Left alone: sqldatabase/z" not in s
    ):
        problems.append(f"Shape's cleanup: exit {sc}, out {s!r}, err {serr!r}")
    return problems


def probe_cleanup_dry_run_files(tmp: Path) -> Problems:
    """dry-run-judges-files (#701): the baseline's dry run lists a recorded local file that is
    already gone as one it would remove; Shape's dry run leaves it alone, as its real cleanup does,
    and still lists a file that is there as one it would remove (and leaves it in place)."""
    home = Home(tmp)
    problems: Problems = []
    gone = tmp / "base" / "gone.csv"
    home.put_session(
        record(
            "abab1212",
            artifacts=[{"target": "file", "name": "g", "row_count": 1, "detail": str(gone)}],
        )
    )
    bc, b, _ = home.baseline("cleanup", "abab1212", "--dry-run")
    if bc != 0 or "  [dry-run] Would remove: file/g" not in b.splitlines():
        problems.append(f"the baseline's dry run no longer lists a gone file: exit {bc}, {b!r}")
    folder = tmp / "session"
    folder.mkdir()
    (folder / ".shape-demo-session").write_text("cdcd3434\n", encoding="utf-8")
    kept = folder / "kept.csv"
    kept.write_text("a\n1\n", encoding="utf-8")
    artifacts = [
        {"target": "file", "name": "kept", "row_count": 1, "detail": str(kept)},
        {"target": "file", "name": "gone", "row_count": 1, "detail": str(folder / "gone.csv")},
    ]
    home.put_session(record("cdcd3434", artifacts=artifacts))
    sc, s, _ = home.shape_cmd("cleanup", "cdcd3434", "--dry-run")
    want = ["  [dry-run] Would remove: file/kept", "  Left alone: file/gone (already gone)"]
    if sc != 0 or s.splitlines() != want or not kept.exists():
        problems.append(f"Shape's dry run: exit {sc}, {s!r}, kept file there: {kept.exists()}")
    sc, s, _ = home.shape_cmd("cleanup", "cdcd3434")
    if sc != 0 or "  Left alone: file/gone (already gone)" not in s.splitlines():
        problems.append(f"Shape's cleanup does not leave the gone file alone: exit {sc}, {s!r}")
    return problems


def probe_file_removal(tmp: Path) -> Problems:
    home = Home(tmp)
    victims = {"b": tmp / f"my_{_refpkg.NAME}_notes", "s": tmp / f"my_{_refpkg.NAME}_notes_shape"}
    for v in victims.values():
        (v / "data").mkdir(parents=True)
        (v / "data" / "f.txt").write_text("keep me")
    for sid, v in (("ffff6666", victims["b"]),):
        (home.base_sessions).mkdir(parents=True, exist_ok=True)
        (home.base_sessions / f"demo-{sid}.json").write_text(
            json.dumps(
                record(
                    sid,
                    artifacts=[{"target": "file", "name": "n", "row_count": 0, "detail": str(v)}],
                )
            )
        )
    (home.shape_sessions).mkdir(parents=True, exist_ok=True)
    (home.shape_sessions / "demo-ffff6666.json").write_text(
        json.dumps(
            record(
                "ffff6666",
                artifacts=[
                    {"target": "file", "name": "n", "row_count": 0, "detail": str(victims["s"])}
                ],
            )
        )
    )
    problems: Problems = []
    home.baseline("cleanup", "ffff6666")
    if victims["b"].exists():
        problems.append("the baseline no longer removes a folder that has `refengine` in its name")
    sc, _, _ = home.shape_cmd("cleanup", "ffff6666")
    if sc != 0 or not (victims["s"] / "data" / "f.txt").exists():
        problems.append("Shape removed (or failed on) a folder its session did not create")
    return problems


def probe_spark_artifacts(tmp: Path) -> Problems:
    source = read("demo/modes/seeding.py")
    spark = source[source.index("def _run_spark") : source.index("def _available_targets")]
    return [] if "add_artifact" not in spark else ["the baseline's Spark run now records artifacts"]


def probe_rollback(tmp: Path) -> Problems:
    seeding = read("demo/modes/seeding.py")
    orch = read("demo/orchestrator.py")
    problems: Problems = []
    if "CleanupEngine" in seeding:
        problems.append("the baseline's seeding mode now cleans up after itself")
    handler = orch[orch.index("except Exception as e:") :]
    if (
        "CleanupEngine" not in handler
        or "CleanupEngine"
        in orch[: orch.index("except Exception as e:")].split("class DemoOrchestrator")[1]
    ):
        problems.append("the baseline's rollback is no longer only in the exception handler")
    if 'return {"success": False, "error": str(e)}' not in seeding:
        problems.append("the baseline's seeding mode no longer turns an exception into a result")
    return problems


def probe_silent_skip(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    got = baseline_probe("silent_skip")
    if got != {"sinks": 0, "sinks_list": 0}:
        problems.append(f"the baseline no longer skips a target silently: {got}")
    code, _, err = home.shape_cmd(
        "init",
        "--name",
        "half",
        "--workspace-id",
        "w",
        "--warehouse-conn",
        "Driver={x};Server=s;Database=d",
    )
    code, _, err = home.shape_cmd(
        "run", "retail", "--mode", "seeding", "--connection", "half", "--rows", "1000"
    )
    if code != 1 or "no warehouse_staging_path" not in err:
        problems.append(f"Shape ran with a Warehouse it cannot load: exit {code}, {err[-200:]!r}")
    return problems


def probe_secret_at_rest(tmp: Path) -> Problems:
    home = Home(tmp)
    conn = "Driver={x};Server=s;Database=d;UID=u;PWD=hunter2"
    problems: Problems = []
    bc, _, _ = home.baseline(
        "init",
        "--name",
        "p",
        "--warehouse-conn",
        conn,
        "--workspace-id",
        "",
        "--eventhouse-uri",
        "",
        "--sql-db-conn",
        "",
        "--lakehouse-id",
        "",
    )
    stored = (home.base / f".{_refpkg.NAME}" / "connections.json").read_text() if bc == 0 else ""
    if "hunter2" not in stored:
        problems.append("the baseline no longer stores a password in connections.json")
    sc, _, err = home.shape_cmd("init", "--name", "p", "--warehouse-conn", conn)
    if sc != 2 or "hunter2" in err or (home.shape / "connections.json").exists():
        problems.append(f"Shape stored or echoed a password: exit {sc}")
    sc, _, err = home.shape_cmd(
        "init", "--name", "p", "--auth", "spn", "--client-secret", "literal-secret"
    )
    if sc != 2 or "literal-secret" in err:
        problems.append("Shape took a literal client secret")
    return problems


def probe_preflight(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    uri = "http://127.0.0.1:9"  # nothing listens here
    home.baseline(
        "init",
        "--name",
        "k",
        "--workspace-id",
        "",
        "--warehouse-conn",
        "",
        "--eventhouse-uri",
        uri,
        "--sql-db-conn",
        "Driver={x};Server=s",
        "--lakehouse-id",
        "lh",
    )
    bc, b, _ = home.baseline("preflight", "--connection", "k")
    if not (
        bc == 0
        and "[OK] Eventhouse: URI set" in b
        and "[OK] SQL DB: Connection string set" in b
        and "[OK] Lakehouse" in b
    ):
        problems.append(
            f"the baseline's preflight no longer reports unchecked targets as OK: {b!r}"
        )
    home.shape_cmd(
        "init",
        "--name",
        "k",
        "--workspace-id",
        "ws",
        "--eventhouse-uri",
        uri,
        "--eventhouse-database",
        "db",
        "--lakehouse-id",
        "lh",
    )
    sc, s, _ = home.shape_cmd("preflight", "--connection", "k")
    if sc != 1 or "[FAIL] Eventhouse" not in s or "[OK] Eventhouse" in s:
        problems.append(f"Shape's preflight passed an Eventhouse nothing answers: exit {sc}, {s!r}")
    return problems


def probe_output_format(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    bc, _, _ = home.baseline(
        "run", "retail", "--mode", "seeding", "--rows", "1000", "--dry-run", "--output", "nonsense"
    )
    if bc != 0:
        problems.append("the baseline now refuses an unknown output format")
    sc, _, err = home.shape_cmd(
        "run", "retail", "--mode", "seeding", "--rows", "1000", "--dry-run", "--output", "nonsense"
    )
    if sc != 2 or "unknown output format" not in err:
        problems.append(f"Shape accepted an unknown output format: exit {sc}")
    return problems


def probe_rows_zero(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    bc, b, _ = home.baseline("run", "retail", "--mode", "seeding", "--rows", "0", "--dry-run")
    if bc != 0 or "100,000 rows" not in b:
        problems.append("the baseline no longer reads --rows 0 as the default")
    sc, _, err = home.shape_cmd("run", "retail", "--mode", "seeding", "--rows", "0", "--dry-run")
    if sc != 2 or "rows must be at least 1" not in err:
        problems.append(f"Shape accepted --rows 0: exit {sc}")
    return problems


def probe_report_escaping(tmp: Path) -> Problems:
    home = Home(tmp)
    evil = record(
        "gggg7777", scenario="<script>alert(1)</script>", mode="a|b", error="<b>x</b>",
        success=False,
        artifacts=[{"target": "t|x", "name": "<img src=x>", "row_count": 1, "detail": ""}],
    )  # fmt: skip
    home.put_session(evil)
    problems: Problems = []
    _, bh, _ = home.baseline("report", "gggg7777", "--format", "html")
    _, bm, _ = home.baseline("report", "gggg7777")
    if "<script>alert(1)</script>" not in bh or "<img src=x>" not in bh or "| a|b |" not in bm:
        problems.append("the baseline now escapes its reports")
    _, sh, _ = home.shape_cmd("report", "gggg7777", "--format", "html")
    _, sm, _ = home.shape_cmd("report", "gggg7777")
    if (
        "<script>" in sh
        or "<img src=x>" in sh
        or "&lt;script&gt;" not in sh
        or "| a\\|b |" not in sm
    ):
        problems.append("Shape's reports are not escaped")
    return problems


def probe_session_id(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    for sessions in (home.base_sessions, home.shape_sessions):
        (sessions / "demo-a").mkdir(parents=True, exist_ok=True)
        (sessions / "demo-a" / "b.json").write_text(json.dumps(record("a/b")))
    bc, _, _ = home.baseline("status", "a/b")
    if bc != 0:
        problems.append("the baseline no longer reads a record by a path")
    sc, _, err = home.shape_cmd("status", "a/b")
    if sc != 2 or "not a plain name" not in err:
        problems.append(f"Shape took a path as a session id: exit {sc}")
    return problems


def probe_exit_codes(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    for args, label in (
        (["run", "nope"], "an unknown scenario"),
        (["run", "enterprise", "--mode", "inference"], "an unsupported mode"),
        (["run", "retail", "--connection", "ghost"], "an unknown profile"),
    ):
        bc, _, berr = home.baseline(*args)
        sc, _, serr = home.shape_cmd(*args)
        if bc != 1 or "Traceback" not in berr:
            problems.append(f"{label}: the baseline's exit is no longer 1 with a traceback ({bc})")
        if sc != 2 or "Traceback" in serr or not serr.startswith("shape: error: "):
            problems.append(f"{label}: Shape's exit {sc}: {serr[:120]!r}")
    for command in ("status", "report", "cleanup"):
        bc, _, _ = home.baseline(command, "nothere1")
        sc, _, serr = home.shape_cmd(command, "nothere1")
        if bc != 1:
            problems.append(f"{command}: the baseline's exit for a missing session is {bc}")
        if sc != 2 or "no session 'nothere1'" not in serr:
            problems.append(f"{command}: Shape's exit {sc}: {serr[:120]!r}")
    return problems


def probe_streaming(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    bc, b, _ = home.baseline("run", "retail", "--mode", "streaming", "--seed", "43")
    events = [x for x in b.splitlines() if x.startswith("{")]
    if bc != 0 or len(events) < 5000 or f"{_refpkg.FIELD_PREFIX}table" not in events[0]:
        problems.append(f"the baseline's streaming run changed: {len(events)} events")
    sc, s, _ = home.shape_cmd(
        "run", "retail", "--mode", "streaming", "--max-events", "5", "--seed", "1"
    )
    mine = [x for x in s.splitlines() if x.startswith("{")]
    if sc != 0 or len(mine) != 5 or "_shape_table" not in mine[0] or f"_{_refpkg.NAME}" in s:
        problems.append(f"Shape's streaming run: exit {sc}, {len(mine)} events")
    return problems


def probe_outputs(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    bdir, sdir = tmp / "b", tmp / "s"
    bdir.mkdir()
    sdir.mkdir()
    home.baseline(
        "run", "retail", "--rows", "1000", "--seed", "43", "--output", "semantic_model", cwd=bdir
    )
    if list(bdir.iterdir()):
        problems.append(
            f"the baseline now writes a semantic model: {[p.name for p in bdir.iterdir()]}"
        )
    code, _, err = home.shape_cmd(
        "run",
        "retail",
        "--rows",
        "1000",
        "--seed",
        "1",
        "--output",
        "charts,semantic_model",
        "--output-dir",
        str(sdir),
    )
    if code != 0 or sorted(p.name for p in sdir.iterdir()) != [
        "retail_charts.html",
        "retail_model.bim",
    ]:
        problems.append(
            f"Shape's files: exit {code}, {sorted(p.name for p in sdir.iterdir())}, {err[-200:]}"
        )
    return problems


# ---------------------------------------------------------------------------------------------
# wrong command lines


def check_exit_codes(tmp: Path) -> Problems:
    home = Home(tmp)
    problems: Problems = []
    for args in (["bogus"], ["run"], ["run", "retail", "--mode", "nope"], ["cleanup"], ["status"]):
        sc, _, _ = home.shape_cmd(*args)
        if sc != 2:
            problems.append(f"shape demo {' '.join(args)}: exit {sc}, expected 2")
    return problems


# ---------------------------------------------------------------------------------------------
# negative control


def negative_control(tmp: Path) -> Problems:
    """Each tampering of Shape's output must be flagged by the same comparison."""
    problems: Problems = []

    def expect_flagged(name: str, found: Problems) -> None:
        if not found:
            problems.append(f"NOT FLAGGED: {name}")

    # the fidelity report
    a, b, b2 = datasets(tmp)
    base = baseline_probe("fidelity", str(a), str(b2))
    mine = shape_probe("fidelity", str(a), str(b2))
    if compare_fidelity(base, mine):
        raise RunError("the untampered fidelity reports differ; the control cannot run")
    for name, tamper in (
        ("a verdict flipped", lambda m: m["rows"][0].update({"pass": not m["rows"][0]["pass"]})),
        ("a null rate changed", lambda m: m["rows"][3].update(real_nulls="99.9%")),
        ("a cardinality changed", lambda m: m["rows"][3].update(syn_card="-1")),
        ("a column dropped", lambda m: m["rows"].pop()),
        ("the score changed", lambda m: m.update(score=m["score"] - 0.1)),
    ):
        bad = copy.deepcopy(mine)
        tamper(bad)
        expect_flagged(f"fidelity: {name}", compare_fidelity(base, bad))

    # the inference run
    source = tmp / "inf-source"
    baseline_probe("dataset", str(source), "43")
    ref, seeds, run_mine = inference_runs(tmp / "inf", "retail", 1000, source / "customer.parquet")
    if compare_inference(ref, seeds, run_mine):
        raise RunError("the untampered inference run differs; the control cannot run")

    def tampered(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
        bad = {
            "info": copy.deepcopy(run_mine["info"]),
            "tables": {k: v.copy() for k, v in run_mine["tables"].items()},
        }
        change(bad)
        return bad

    def scale_column(r: dict[str, Any]) -> None:
        r["tables"]["customer"]["customer_id"] = r["tables"]["customer"]["customer_id"] * 1.5

    def collapse_category(r: dict[str, Any]) -> None:
        r["tables"]["customer"]["loyalty_tier"] = "X"

    def null_out(r: dict[str, Any]) -> None:
        r["tables"]["customer"].loc[:, "email"] = None

    def drop_column(r: dict[str, Any]) -> None:
        r["info"]["tables"]["customer"]["columns"].pop()
        r["tables"]["customer"] = r["tables"]["customer"].iloc[:, :-1]

    def drop_rows(r: dict[str, Any]) -> None:
        r["info"]["tables"]["customer"]["rows"] -= 5
        r["tables"]["customer"] = r["tables"]["customer"].iloc[:-5]

    def wrong_score(r: dict[str, Any]) -> None:
        r["info"]["score"] = 0.2

    def wrong_artifact_rows(r: dict[str, Any]) -> None:
        r["info"]["artifacts"][0]["row_count"] += 1

    for name, change in (
        ("a numeric column scaled by 1.5", scale_column),
        ("a categorical column collapsed", collapse_category),
        ("a column set to null", null_out),
        ("a column dropped", drop_column),
        ("rows dropped", drop_rows),
        ("the fidelity score changed", wrong_score),
        ("an artifact's rows changed", wrong_artifact_rows),
    ):
        expect_flagged(f"inference: {name}", compare_inference(ref, seeds, tampered(change)))

    def replace_in_code(d: dict[str, Any], old: str, new: str) -> None:
        for c in d["cells"]:
            if c["cell_type"] == "code":
                c["source"] = [x.replace(old, new) for x in c["source"]]

    # the notebook
    home = Home(tmp / "nb")
    b_path, s_path = tmp / "nb-b.ipynb", tmp / "nb-s.ipynb"
    home.baseline("notebook", "retail", "--mode", "seeding", "--output", str(b_path))
    home.shape_cmd("notebook", "retail", "--mode", "seeding", "--output", str(s_path))
    bnb, snb = json.loads(b_path.read_text()), json.loads(s_path.read_text())
    if notebook_differences(bnb, snb):
        raise RunError("the untampered notebooks differ; the control cannot run")
    for name, change in (
        ("a markdown cell changed", lambda d: d["cells"][0]["source"].append("extra")),
        ("a cell dropped", lambda d: d["cells"].pop(0)),
        ("the rows changed", lambda d: replace_in_code(d, "rows=", "rows=9")),
        ("the scenario changed", lambda d: replace_in_code(d, "'retail'", "'other'")),
        ("the format changed", lambda d: d.update(nbformat=3)),
    ):
        bad = copy.deepcopy(snb)
        change(bad)
        expect_flagged(f"notebook: {name}", notebook_differences(bnb, bad))

    # the description (description-names-what-runs)
    old_text, new_text = DESCRIPTIONS["adventureworks"]
    b_path, s_path = tmp / "aw-b.ipynb", tmp / "aw-s.ipynb"
    home.baseline("notebook", "adventureworks", "--mode", "seeding", "--output", str(b_path))
    home.shape_cmd("notebook", "adventureworks", "--mode", "seeding", "--output", str(s_path))
    bnb, snb = json.loads(b_path.read_text()), json.loads(s_path.read_text())
    if notebook_differences(bnb, snb):
        raise RunError("the untampered adventureworks notebooks differ; the control cannot run")
    bad = copy.deepcopy(snb)
    bad["cells"][0]["source"] = [x.replace(new_text, old_text) for x in bad["cells"][0]["source"]]
    expect_flagged(
        "notebook: the baseline's adventureworks description", notebook_differences(bnb, bad)
    )

    # the estimate (rows-are-what-runs)
    home = Home(tmp / "est")
    written = baseline_written_rows(1000)
    args = ["run", "retail", "--mode", "seeding", "--rows", "1000", "--dry-run"]
    _, b, _ = home.baseline(*args)
    _, s, _ = home.shape_cmd(*args)
    want = estimate_lines(brand(baseline_as_run(b, 1000, written, True)))
    if diff(want, estimate_lines(s)):
        raise RunError("the untampered estimates differ; the control cannot run")
    for name, change in (
        (
            "--rows printed as the size",
            lambda t: t.replace(f"small scale preset: {written:,} rows", "1,000 rows"),
        ),
        ("the preset's rows off by one", lambda t: t.replace(f"{written:,}", f"{written + 1:,}")),
        ("the cost changed", lambda t: re.sub(r"CU-minutes: [0-9.]+", "CU-minutes: 9.9", t)),
    ):
        expect_flagged(f"estimate: {name}", diff(want, estimate_lines(change(s))))

    # the cleanup dry run (dry-run-judges-files)
    base, mine = cleanup_dry_runs(tmp / "cln")
    if diff(base, mine):
        raise RunError("the untampered cleanup dry runs differ; the control cannot run")
    for name, change in (
        (
            "a gone file listed as removed",
            lambda t: [
                "  [dry-run] Would remove: file/a" if x.startswith("  Left alone: file/a") else x
                for x in t
            ],
        ),
        (
            "another reason",
            lambda t: [x.replace("(already gone)", "(not inside a folder)") for x in t],
        ),
        (
            "a warehouse table left alone",
            lambda t: [
                "  Left alone: warehouse/b (already gone)"
                if x == "  [dry-run] Would remove: warehouse/b"
                else x
                for x in t
            ],
        ),
    ):
        expect_flagged(f"cleanup --dry-run: {name}", diff(base, change(mine)))

    # the record's report
    home = Home(tmp / "rep")
    home.put_session(
        record(
            "aaaa1111", artifacts=[{"target": "file", "name": "t", "row_count": 7, "detail": ""}]
        )
    )
    _, b, _ = home.baseline("report", "aaaa1111")
    _, s, _ = home.shape_cmd("report", "aaaa1111")
    if brand(b) != s:
        raise RunError("the untampered reports differ; the control cannot run")
    for name, change in (
        ("a row count changed", lambda t: t.replace("| 7 |", "| 8 |")),
        ("a row dropped", lambda t: t.replace("| file | t | 7 |\n", "")),
        ("the status changed", lambda t: t.replace("SUCCESS", "FAILED")),
    ):
        expect_flagged(f"report: {name}", [] if change(s) == brand(b) else ["differs"])
    return problems


# ---------------------------------------------------------------------------------------------


CHECKS: tuple[tuple[str, Callable[[Path], Problems]], ...] = (
    ("catalog: scenarios, modes, domains, rows, tags", check_catalog),
    ("list: one line per scenario", check_list_cli),
    ("options and choices of every command", check_options),
    ("fidelity report: every row and the score, on two dataset pairs", check_fidelity),
    ("inference run (domain defaults): tables, rows, record, score", check_inference),
    ("inference run (a file): same schema, T-21 (a)-(d) on the values", check_inference_file),
    ("seeding run: preset rows and T-21 (a)-(h) via domain_1to1/verify.py", check_seeding_data),
    ("status, Markdown and HTML report of the same records", check_status_report),
    ("cost estimate and dry-run line", check_estimate),
    ("cleanup --dry-run lines", check_cleanup_dry_run),
    ("init: the stored profile", check_init),
    ("notebooks: markdown, cell kinds, scenario, mode, rows", check_notebook),
    ("wrong command lines fail in Shape (exit 2)", check_exit_codes),
    ("probe: the scenario's domain is ignored", probe_scenario_domain),
    ("probe: one row count for every table", probe_row_counts),
    ("probe: --dry-run runs for real", probe_dry_run),
    ("probe: the estimate prints --rows, the run writes the preset", probe_estimate_rows),
    ("probe: a description names tables the run never makes", probe_descriptions),
    ("probe: cleanup reports the unremoved as removed", probe_cleanup_honesty),
    ("probe: cleanup --dry-run lists a gone file", probe_cleanup_dry_run_files),
    ("probe: cleanup removes by name", probe_file_removal),
    ("probe: a Spark run records nothing", probe_spark_artifacts),
    ("probe: a failed seeding run is not rolled back", probe_rollback),
    ("probe: a target is skipped silently", probe_silent_skip),
    ("probe: a password at rest", probe_secret_at_rest),
    ("probe: preflight reports what it did not check", probe_preflight),
    ("probe: an unknown output format", probe_output_format),
    ("probe: --rows 0", probe_rows_zero),
    ("probe: report escaping", probe_report_escaping),
    ("probe: the session id", probe_session_id),
    ("probe: exit codes and messages", probe_exit_codes),
    ("probe: streaming", probe_streaming),
    ("probe: charts and the semantic model", probe_outputs),
    ("probe: the HTML page's charset", check_report),
)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--negative-control", action="store_true", help="also tamper and require a flag"
    )
    ap.add_argument("--only", metavar="TEXT", help="run the checks whose title has TEXT")
    ap.add_argument("--keep", metavar="DIR", help="work in DIR and keep it")
    args = ap.parse_args(argv)
    if not REFENGINE_ROOT.exists() or not SHAPE_CLI.exists():
        print(
            "ERROR: the baseline or Shape venv is missing: run setup_refengine.sh and pip install",
            file=sys.stderr,
        )
        return 2
    failed = 0
    with tempfile.TemporaryDirectory() as scratch:
        tmp = Path(args.keep) if args.keep else Path(scratch)
        tmp.mkdir(parents=True, exist_ok=True)
        checks = list(CHECKS)
        if args.negative_control:
            checks.append(("negative control: tampering is flagged", negative_control))
        for title, check in checks:
            if args.only and args.only.lower() not in title.lower():
                continue
            sub = tmp / re.sub(r"\W+", "-", title)[:40]
            sub.mkdir(parents=True, exist_ok=True)
            try:
                problems = check(sub)
            except RunError as exc:
                print(f"ERROR {title}: {exc}")
                return 2
            print(f"{'PASS' if not problems else 'FAIL'}  {title}", flush=True)
            for p in problems[:12]:
                print(f"        {p}")
            if len(problems) > 12:
                print(f"        ... {len(problems) - 12} more")
            failed += bool(problems)
    for note in NOTES:
        print(f"note: {note}")
    print(f"\nallowed differences: {', '.join(d.name for d in ALLOWED)}")
    print(f"VERDICT: {'PASS' if not failed else 'FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
