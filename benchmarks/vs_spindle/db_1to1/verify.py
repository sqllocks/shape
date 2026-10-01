"""T-22 parity: Shape's database profiling vs the pinned baseline's database profiler.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/db_1to1/verify.py \
        [--case NAME ...] [--mssql CONNECTION_STRING] [--refresh]

Both implementations read the same database. Without ``--mssql`` that is the in-memory
FakeConnection of ``shape_sqlserver.testing`` (deterministic scenarios; no server needed).
With ``--mssql`` each scenario is first loaded into a scratch schema of a real SQL Server
(``parity_<scenario>``, dropped afterwards), and both profile it through pyodbc; the Spindle
venv then needs pyodbc (``"$SPINDLE_PY" -m pip install pyodbc``).

Every column and table field is compared with the rules of ``profile_1to1/verify.py`` (T-22).
Equivalence only: nothing is timed (database profiling time is dominated by the server).
Exits 0 when every case matches, 1 on any mismatch.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "profile_1to1"))
import verify as profile_verify  # noqa: E402  (profile_1to1/verify.py: RULES and compare)
from db_dump import load_testing  # noqa: E402
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
    import pyodbc

    testing = load_testing()
    fake = testing.scenario(scenario, scale)
    schema = f"parity_{scenario}"
    with pyodbc.connect(mssql, autocommit=True) as conn:
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


def compare_profiles(sp: dict, sh: dict, label: str, matrix: dict, fails: list) -> None:
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
                sp["tables"][t], sh["tables"][t], f"{label}:{t}", matrix, fails
            )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--case", action="append", choices=list(CASES))
    ap.add_argument("--mssql", metavar="CONNECTION_STRING")
    a = ap.parse_args()
    names = a.case or list(CASES)
    all_fails: dict[str, list] = {}
    cells = checked = 0
    for case in names:
        scenario, scale, kw, fail = CASES[case]
        if a.mssql:
            if fail:
                continue  # unreadable tables are simulated by the fake only
            load_real(a.mssql, scenario, scale)
        sp = spindle_side(case, scenario, scale, kw, fail, a.mssql)
        sh = shape_side(scenario, scale, kw, fail, a.mssql)
        matrix: dict = {}
        fails: list = []
        compare_profiles(sp, sh, case, matrix, fails)
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
    print(f"\n{checked - bad}/{checked} cases match ({cells} field comparisons)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
