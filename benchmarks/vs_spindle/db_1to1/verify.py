"""T-22 parity: Shape's database profiling vs the pinned baseline's database profiler.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/db_1to1/verify.py \
        [--case NAME ...] [--mssql CONNECTION_STRING] [--refresh]

Both implementations read the same database. Without ``--mssql`` that is the in-memory
FakeConnection of ``shape_sqlserver.testing`` (deterministic scenarios; no server needed).
With ``--mssql`` each scenario is first loaded into a scratch schema of a real SQL Server
(``parity_<scenario>``, dropped afterwards), and both profile it through pyodbc; the Spindle
venv then needs pyodbc (``"$SPINDLE_PY" -m pip install pyodbc``).

Every column and table field is compared with the rules of ``profile_1to1/verify.py`` (T-22).
Three baseline defects are fixed on purpose (see ``intentional.py``): the baseline's profile is
first turned into the profile the corrected behaviour produces, by a narrow, named allow-list of
fields, and Shape must equal that; every other field must equal the baseline as it is. The
adjustments are counted and printed.
Equivalence only: nothing is timed (database profiling time is dominated by the server).
Exits 0 when every case matches, 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "profile_1to1"))
import verify as profile_verify  # noqa: E402  (profile_1to1/verify.py: RULES and compare)
from db_dump import load_testing  # noqa: E402
from intentional import DEFAULT_SAMPLE_ROWS, corrected_baseline  # noqa: E402
from paths import BENCH_OUT_DIR, SPINDLE_PY  # noqa: E402

# name -> (scenario, scale, profile() arguments, tables that fail to read)
CASES = {
    "retail": ("retail", 1, {}, ()),
    "retail_no_sample": ("retail", 1, {"sample_rows": 0}, ()),
    "retail_small_sample": ("retail", 1, {"sample_rows": 100}, ()),
    "retail_subset": ("retail", 1, {"tables": ["orders", "customer"]}, ()),
    "retail_unreadable": ("retail", 1, {}, ("product",)),
    "warehouse": ("warehouse", 1, {}, ()),
    "warehouse_empty": ("warehouse_empty", 1, {}, ()),
    "name_only": ("name_only", 1, {}, ()),
    "id_named": ("id_named", 1, {}, ()),
}
OUT = BENCH_OUT_DIR / "db_1to1"


def spindle_side(case: str, scenario: str, scale: int, kw: dict, fail: tuple, mssql: str | None):
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"spindle_{case}.json"
    cmd = [str(SPINDLE_PY), str(HERE / "db_dump.py"), str(out)]
    if mssql:
        cmd += ["--connection-string", mssql, "--schema", f"parity_{scenario}"]
    else:
        cmd += ["--scenario", scenario, "--scale", str(scale), "--fail-reads", ",".join(fail)]
    if "sample_rows" in kw:
        cmd += ["--sample-rows", str(kw["sample_rows"])]
    if "tables" in kw:
        cmd += ["--tables", ",".join(kw["tables"])]
    subprocess.run(cmd, check=True)
    return json.loads(out.read_text())


def shape_side(scenario: str, scale: int, kw: dict, fail: tuple, mssql: str | None):
    from shape_sqlserver import Credentials, profile_database

    if mssql:
        prof = profile_database(
            mssql, credentials=Credentials("sql"), schema=f"parity_{scenario}", **kw
        )
    else:
        conn = load_testing().scenario(scenario, scale)
        conn.fail_reads = set(fail)
        prof = profile_database(connection=conn, **kw)
    return json.loads(json.dumps(prof.to_dict(), default=str))


def load_real(mssql: str, scenario: str, scale: int) -> None:
    import contextlib

    import pyodbc

    pyodbc.pooling = False  # a pooled session would keep its locks after close
    testing = load_testing()
    fake = testing.scenario(scenario, scale)
    schema = f"parity_{scenario}"
    with contextlib.closing(pyodbc.connect(mssql, autocommit=True)) as conn:
        cur = conn.cursor()
        drop_real(cur, fake, schema)
        cur.execute(f"CREATE SCHEMA [{schema}]")
        for stmt in testing.ddl(fake, schema):
            cur.execute(stmt)
        for table in fake.tables:
            testing.insert_rows(cur, table, schema)


def drop_real(cur, fake, schema: str) -> None:
    for fk in fake.foreign_keys:
        cur.execute(
            f"IF OBJECT_ID('[{schema}].[{fk.child_table}]') IS NOT NULL "
            f"ALTER TABLE [{schema}].[{fk.child_table}] DROP CONSTRAINT IF EXISTS [{fk.name}]"
        )
    for table in reversed(fake.tables):
        cur.execute(f"DROP TABLE IF EXISTS [{schema}].[{table.name}]")
    cur.execute(f"IF SCHEMA_ID('{schema}') IS NOT NULL DROP SCHEMA [{schema}]")


def compare_profiles(
    sp: dict, sh: dict, label: str, matrix: dict, fails: list, sampled: dict[str, int] | None = None
) -> None:
    ok = sp["relationships"] == sh["relationships"]
    matrix.setdefault("dataset.relationships", [0, 0, 0])
    matrix["dataset.relationships"][0] += 1
    matrix["dataset.relationships"][1] += ok
    matrix["dataset.relationships"][2] += ok
    if not ok:
        fails.append(
            f"{label} relationships: baseline={sp['relationships']} shape={sh['relationships']}"
        )
    if list(sp["tables"]) != list(sh["tables"]):
        fails.append(
            f"{label} table list: baseline={list(sp['tables'])} shape={list(sh['tables'])}"
        )
    for t in sp["tables"]:
        if t in sh["tables"]:
            profile_verify.check_table(
                sp["tables"][t],
                sh["tables"][t],
                f"{label}:{t}",
                matrix,
                fails,
                # the enum rule (P1-18) counts the sample's non-null values
                (lambda _tbl, col, n=sampled[t]: n - col["null_count"]) if sampled else None,
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", action="append", choices=list(CASES))
    ap.add_argument("--mssql", metavar="CONNECTION_STRING")
    a = ap.parse_args()
    names = a.case or list(CASES)
    loaded: set[str] = set()
    all_fails: dict[str, list] = {}
    cells = checked = 0
    tally: Counter = Counter()
    for case in names:
        scenario, scale, kw, fail = CASES[case]
        if a.mssql:
            if fail:
                continue  # unreadable tables are simulated by the fake only
            if scenario not in loaded:
                load_real(a.mssql, scenario, scale)
                loaded.add(scenario)
        sp = spindle_side(case, scenario, scale, kw, fail, a.mssql)
        sh = shape_side(scenario, scale, kw, fail, a.mssql)
        matrix: dict = {}
        fails: list = []
        declared = bool(load_testing().scenario(scenario, scale).foreign_keys)
        want, sampled = corrected_baseline(
            sp, case, kw.get("sample_rows", DEFAULT_SAMPLE_ROWS), fail, declared, tally
        )
        for t, n in sampled.items():
            got = sh["tables"][t].get("sampled_rows")
            if got != n:
                fails.append(f"{case}:{t} sampled_rows: expected={n} shape={got}")
        compare_profiles(want, sh, case, matrix, fails, sampled)
        all_fails[case] = fails
        n = sum(m[0] for m in matrix.values())
        cells += n
        checked += 1
        print(
            f"{case}: {'PASS' if not fails else f'{len(fails)} mismatches'} ({n} comparisons)",
            flush=True,
        )
    for fails in all_fails.values():
        for line in fails:
            print("MISMATCH", line)
    bad = sum(bool(f) for f in all_fails.values())
    print("\nIntentional, documented differences from the baseline (fields changed):")
    for fix in ("FIX-1", "FIX-2", "FIX-3"):
        items = {k: v for k, v in tally.items() if k.startswith(fix)}
        print(f"  {fix}: " + (", ".join(f"{k[6:]}={v}" for k, v in sorted(items.items())) or "-"))
    if not a.case:  # a full run must exercise every fix, or the allow-list proves nothing
        for fix in ("FIX-1", "FIX-2", "FIX-3"):
            if not any(v for k, v in tally.items() if k.startswith(fix)):
                print(f"MISMATCH no {fix} difference was exercised")
                bad += 1
    enum = profile_verify.ENUM_TALLY
    print(
        f"  P1-18 enum rule ({', '.join(profile_verify.ENUM_RULE_FIELDS)}): "
        f"{enum['flipped']} columns no longer enums, {enum['kept']} stay enums"
    )
    if not a.case:  # a full run must exercise the rule both ways, or the allow-list proves nothing
        for k, what in (("flipped", "turned a baseline enum off"), ("kept", "kept an enum")):
            if not enum[k]:
                print(f"MISMATCH the enum rule never {what}")
                bad += 1
    print(f"\n{checked - bad}/{checked} cases match ({cells} field comparisons)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
