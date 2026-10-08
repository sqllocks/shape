"""P4-06 acceptance: Shape's Parquet output reads back in the pinned baseline's environment.

    source scripts/env.sh && python benchmarks/vs_refengine/writers_1to1/verify.py

Shape writes a table set through the ``parquet`` sink (Shape venv, this process). The baseline
venv (``$REFENGINE_PY``) then reads each file with pandas and runs the baseline's own profiler
(``DataProfiler.profile_dataframe``) on the frame, and the values are compared with the Arrow
tables Shape wrote. Exit 0 only when every file reads back with the same rows, columns and
values. The baseline checkout is only read.
"""

from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import REFENGINE_PY  # noqa: E402

READER = r"""
import json, sys
import pandas as pd
DataProfiler = _refpkg.mod("inference.profiler").DataProfiler

out = {}
for path in sys.argv[1:]:
    df = pd.read_parquet(path)
    prof = DataProfiler().profile_dataframe(df, table_name="t")
    out[path] = {
        "rows": int(len(df)),
        "columns": list(df.columns),
        "nulls": {c: int(df[c].isna().sum()) for c in df.columns},
        "sums": {c: float(df[c].sum()) for c in df.columns if df[c].dtype.kind in "if"},
        "profiled_rows": int(prof.row_count),
        "profiled_columns": list(prof.columns),
    }
print(json.dumps(out))
"""


def sample_tables() -> dict[str, pa.Table]:
    n = 1000
    return {
        "customer": pa.table(
            {
                "customer_id": pa.array(range(1, n + 1), pa.int64()),
                "name": pa.array([f"name {i % 37}" for i in range(n)], pa.string()),
                "score": pa.array([i / 8 for i in range(n)], pa.float64()),
                "active": pa.array([i % 3 == 0 for i in range(n)], pa.bool_()),
                "joined": pa.array(
                    [dt.date(2024, 1, 1) + dt.timedelta(days=i % 300) for i in range(n)]
                ),
                "balance": pa.array([Decimal(i) / 100 for i in range(n)], pa.decimal128(12, 2)),
                "seen": pa.array(
                    [dt.datetime(2025, 1, 1) + dt.timedelta(minutes=i) for i in range(n)],
                    pa.timestamp("us"),
                ),
                "note": pa.array([None if i % 5 == 0 else f"n{i}" for i in range(n)], pa.string()),
            }
        ),
        "order": pa.table(
            {
                "order_id": pa.array(range(1, 501), pa.int64()),
                "customer_id": pa.array([1 + (i * 7) % n for i in range(500)], pa.int64()),
                "total": pa.array([i * 1.25 for i in range(500)], pa.float64()),
            }
        ),
    }


def main() -> int:
    from shape.builtins.sinks import ParquetSink

    if not Path(REFENGINE_PY).exists():
        print(
            f"baseline interpreter not found at {REFENGINE_PY}; run setup_refengine.sh",
            file=sys.stderr,
        )
        return 2
    tables = sample_tables()
    with tempfile.TemporaryDirectory() as tmp:
        files = {}
        for name, table in tables.items():
            target = Path(tmp) / f"{name}.parquet"
            ParquetSink().write(str(target), name, iter(table.to_batches(max_chunksize=300)))
            files[name] = target
        proc = subprocess.run(
            [str(REFENGINE_PY), "-c", READER, *map(str, files.values())],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            print(proc.stderr, file=sys.stderr)
            return 1
        seen = json.loads(proc.stdout)
        failures = []
        for name, table in tables.items():
            got = seen[str(files[name])]
            if got["rows"] != table.num_rows or got["profiled_rows"] != table.num_rows:
                failures.append(f"{name}: row count {got['rows']} != {table.num_rows}")
            if (
                got["columns"] != table.column_names
                or got["profiled_columns"] != table.column_names
            ):
                failures.append(f"{name}: columns {got['columns']}")
            for col in table.column_names:
                if got["nulls"][col] != table[col].null_count:
                    failures.append(f"{name}.{col}: null count")
            for col, total in got["sums"].items():
                want = float(sum(v for v in table[col].to_pylist() if v is not None))
                if abs(total - want) > 1e-9 * max(1.0, abs(want)):
                    failures.append(f"{name}.{col}: sum {total} != {want}")
        for line in failures:
            print("FAIL", line)
        print(f"{len(tables)} parquet files: {'PASS' if not failures else 'FAIL'}")
        return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
