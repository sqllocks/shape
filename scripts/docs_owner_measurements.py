"""Measure narrowly labelled product workloads, checking equivalence before timing."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str], env: dict[str, str] | None = None) -> tuple[str, float]:
    """Run a fresh process, preserving its real output and exit status."""
    start = time.perf_counter()
    p = subprocess.run(command, env=env, text=True, capture_output=True, check=False)
    if p.stderr:
        print(p.stderr, file=sys.stderr, end="")
    if p.returncode:
        print(p.stdout, end="")
        raise RuntimeError(f"Measurement command failed with exit {p.returncode}")
    return p.stdout, time.perf_counter() - start


def emit() -> dict:
    """Compare live/no-live files before timing three pairs of identical workloads."""
    results = []
    command = [
        "shape",
        "emit",
        "retail",
        "--seed",
        "7",
        "--max-events",
        "5000",
        "--sink",
        "file",
        "--fresh",
        "--json",
        "--live-target",
        "retail",
    ]
    for trial in range(4):
        pair = []
        for live in (False, True):
            output = Path(f"events-{trial}-{int(live)}.jsonl")
            cmd = command + ["-o", str(output), "--live-profile" if live else "--no-live-profile"]
            if live:
                cmd.append(f"profile-{trial}.json")
            stdout, seconds = run(cmd)
            report = json.loads(stdout)
            raw = output.read_bytes()
            events = len(raw.splitlines())
            assert events == 5000, report
            pair.append(
                {
                    "live": live,
                    "seconds": seconds,
                    "events": events,
                    "sha256": hashlib.sha256(raw).hexdigest(),
                }
            )
        assert pair[0]["sha256"] == pair[1]["sha256"], (
            "Live tee changed emitted events; no timing accepted"
        )
        if trial:
            results.append(pair)
    return {
        "workload": (
            "retail, seed=7, 5000 events, file emitter, paired live/no-live, fresh process per run"
        ),
        "verification": "Warm-up pair and every timed pair emitted 5000 byte-identical events",
        "trials": results,
    }


def allocator_worker() -> None:
    """Generate real tables and report their values' IPC digest in a fresh process."""
    import importlib

    # Must precede pyarrow: this is the product import path under measurement.
    shape = importlib.import_module("shape")
    import pyarrow as pa

    from shape.generation.domains import load_domain
    from shape.generation.engine import Engine

    result = Engine(load_domain("retail").schema, scale="small", seed=7).generate()
    digest = hashlib.sha256()
    rows = {}
    for name, table in sorted(result.tables.items()):
        sink = pa.BufferOutputStream()
        with pa.ipc.new_stream(sink, table.schema) as writer:
            writer.write_table(table)
        digest.update(name.encode())
        digest.update(sink.getvalue().to_pybytes())
        rows[name] = table.num_rows
    print(json.dumps({"sha256": digest.hexdigest(), "rows": rows, "shape": shape.__version__}))


def allocator() -> dict:
    """Compare product values before recording fresh-process allocator timings."""
    results = []
    for trial in range(4):
        pair = []
        for pool in ("default", "system"):
            env = {**os.environ, "SHAPE_MEMORY_POOL": "default" if pool == "default" else "system"}
            env.pop("ARROW_DEFAULT_MEMORY_POOL", None)
            stdout, seconds = run(
                [sys.executable, str(Path(__file__).resolve()), "allocator-worker"], env
            )
            pair.append({"pool": pool, "seconds": seconds, **json.loads(stdout)})
        assert pair[0]["sha256"] == pair[1]["sha256"] and pair[0]["rows"] == pair[1]["rows"], (
            "Allocator changed generated values; no timing accepted"
        )
        if trial:
            results.append(pair)
    return {
        "workload": (
            "retail small, seed=7, import shape before pyarrow, full "
            "generation plus IPC digest, fresh process per run"
        ),
        "verification": (
            "Warm-up pair and every timed pair have identical table rows and value digests"
        ),
        "trials": results,
    }


def sqlserver() -> dict:
    """Measure the shipped adapter's exact two sampling queries against real SQL Server."""
    import pyodbc
    from shape_sqlserver.sql import spread_query, top_query

    # The login password is appended only in memory; never print a connection string.
    connection = pyodbc.connect(
        "Driver={ODBC Driver 18 for SQL "
        "Server};Server=localhost,1433;Database=master;UID=sa;Encrypt=yes;TrustServerCertificate=yes;PWD="
        + os.environ["MSSQL_SA_PASSWORD"],
        autocommit=True,
    )
    try:
        cursor = connection.cursor()
        cursor.execute("IF SCHEMA_ID('docs_measure') IS NULL EXEC('CREATE SCHEMA docs_measure')")
        cursor.execute("DROP TABLE IF EXISTS docs_measure.sample")
        cursor.execute("CREATE TABLE docs_measure.sample (id int PRIMARY KEY, amount int NOT NULL)")
        cursor.execute(
            "WITH n AS (SELECT TOP (100000) ROW_NUMBER() OVER (ORDER BY "
            "(SELECT NULL)) AS id FROM sys.all_objects a CROSS JOIN sys.all_objects b) "
            "INSERT INTO docs_measure.sample SELECT id, id * 3 FROM n"
        )
        queries = {
            "first_rows": top_query("docs_measure", "sample", 1000),
            "checksum_spread": spread_query("docs_measure", "sample", 1000, ["id"], ["id"]),
        }
        originals = {}
        # Untimed verification: correct shape, valid values and deterministic repeated samples.
        for name, query in queries.items():
            rows = [tuple(row) for row in cursor.execute(query).fetchall()]
            assert len(rows) == 1000 and len({row[0] for row in rows}) == 1000
            assert all(1 <= row[0] <= 100000 and row[1] == row[0] * 3 for row in rows)
            assert rows == [tuple(row) for row in cursor.execute(query).fetchall()]
            originals[name] = rows
        trials = []
        for _ in range(3):
            pair = {}
            for name, query in queries.items():
                start = time.perf_counter()
                rows = [tuple(row) for row in cursor.execute(query).fetchall()]
                seconds = time.perf_counter() - start
                assert rows == originals[name]
                pair[name] = {
                    "seconds": seconds,
                    "rows": len(rows),
                    "id_min": min(row[0] for row in rows),
                    "id_max": max(row[0] for row in rows),
                }
            trials.append(pair)
        return {
            "workload": (
                "SQL Server container; 100000 rows, int primary key + "
                "amount=id*3; 1000-row samples; warmed connection"
            ),
            "queries": queries,
            "verification": (
                "Both queries return 1000 unique valid rows and repeat the same "
                "sample; timed samples equal their verified samples"
            ),
            "trials": trials,
        }
    finally:
        connection.close()


def main() -> None:
    """Print raw measurements; no generalised speedup or cross-machine claim."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workload", choices=["emit", "allocator", "allocator-worker", "sqlserver"])
    args = parser.parse_args()
    if args.workload == "allocator-worker":
        allocator_worker()
        return
    (ROOT / ".owner-transcript-work").mkdir(exist_ok=True)
    with (ROOT / ".owner-transcript-work/measurement.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        result = {"emit": emit, "allocator": allocator, "sqlserver": sqlserver}[args.workload]()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
