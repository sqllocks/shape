"""Run RefEngine's DatabaseProfiler and dump a normalised JSON profile.

Runs under the *RefEngine* venv:
    source scripts/env.sh && "$REFENGINE_PY" db_dump.py OUT.json --scenario retail [--sample-rows N]
        [--tables a,b] [--fail-reads t1,t2] [--schema dbo]
    source scripts/env.sh && "$REFENGINE_PY" db_dump.py OUT.json --connection-string "$MSSQL" \
        --schema parity_retail            # a real SQL Server (needs pyodbc in the RefEngine venv)

A scenario runs against the in-memory FakeConnection of the plugin's testing module
(plugins/shape-sqlserver/src/shape_sqlserver/testing.py, loaded by path: stdlib only).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "profile_1to1"))
import _refpkg  # noqa: E402
from paths import REFENGINE_ROOT, SHAPE_ROOT  # noqa: E402
from refengine_dump import dataset_to_dict  # noqa: E402

TESTING = SHAPE_ROOT / "plugins" / "shape-sqlserver" / "src" / "shape_sqlserver" / "testing.py"


def load_testing():
    spec = importlib.util.spec_from_file_location("shape_sqlserver_testing", TESTING)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--scenario")
    ap.add_argument("--scale", type=int, default=1)
    ap.add_argument("--connection-string")
    ap.add_argument("--schema", default="dbo")
    ap.add_argument("--sample-rows", type=int, default=1000)
    ap.add_argument("--tables")
    ap.add_argument("--fail-reads", default="")
    ap.add_argument(
        "--rows-pickle",
        help="a pickle {table: rows}: the scenario's tables are replaced by these rows, with the "
        "row counts of the full tables (the baseline then reads exactly these rows)",
    )
    a = ap.parse_args()
    sys.path.insert(0, str(REFENGINE_ROOT))
    DatabaseProfiler = _refpkg.mod("inference.database_profiler").DatabaseProfiler

    tables = a.tables.split(",") if a.tables else None
    if a.scenario:
        testing = load_testing()
        conn = testing.scenario(a.scenario, a.scale)
        conn.fail_reads = {t for t in a.fail_reads.split(",") if t}
        if a.rows_pickle:
            import pickle

            replaced = pickle.loads(Path(a.rows_pickle).read_bytes())  # our own file
            conn.row_counts = {t.name: len(t.rows) for t in conn.tables}
            for t in conn.tables:
                if t.name in replaced:
                    t.rows = list(replaced[t.name])
        dp = DatabaseProfiler(connection=conn).profile(a.schema, a.sample_rows, tables)
    else:
        dp = DatabaseProfiler(connection_string=a.connection_string, auth_method="sql").profile(
            a.schema, a.sample_rows, tables
        )
    Path(a.out).write_text(json.dumps(dataset_to_dict(dp), default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
