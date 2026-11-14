"""Run in a fresh default Fabric Python notebook after the documented wheel install.

Run this file once per row count in a NEW child process so profiling peak RSS excludes
parent generation and previous measurements. Sizes follow the public plan's 5M/50M rows;
this is a Shape-generated ten-column synthetic workload, not the D3 benchmark fixture.
"""
import json
import os
import pathlib
import resource
import subprocess
import sys
import tempfile
import time

import pyarrow.parquet as pq
from shape.generation.engine import Engine
from shape.generation.schema import Column, GenSchema, Model, Table


def run(rows):
    columns = {"event_id": Column("event_id", "integer", {"strategy": "sequence"})}
    for i in range(1, 8):
        columns[f"value_{i}"] = Column(
            f"value_{i}", "float", {"strategy": "uniform", "low": 0, "high": 1000}
        )
    for i in range(8, 10):
        columns[f"category_{i}"] = Column(
            f"category_{i}", "string", {"strategy": "choice", "values": ["a", "b", "c"]}
        )
    schema = GenSchema(Model("f12", "f12", seed=12), {"events": Table("events", columns)})
    engine = Engine(schema, row_counts={"events": rows}, chunk_rows=100_000)
    with tempfile.TemporaryDirectory(prefix="f12-") as folder:
        path = pathlib.Path(folder) / "events.parquet"
        started = time.perf_counter()
        writer = None
        try:
            for batch in engine.iter_chunks("events"):
                if writer is None:
                    writer = pq.ParquetWriter(path, batch.schema)
                writer.write_batch(batch)
        finally:
            if writer is not None:
                writer.close()
        print(json.dumps({"stage": "generation", "rows": rows, "columns": 10,
                          "seconds": time.perf_counter() - started,
                          "parquet_bytes": path.stat().st_size}), flush=True)
        child = r'''
import json, resource, sys, time
from shape.profile.engine import EngineOptions, profile_table
from shape.kernel.dispatch import get_kernel
start = time.perf_counter()
profile = profile_table(sys.argv[1], options=EngineOptions(mode="bounded", threads=2))
print(json.dumps({"stage":"profile", "mode":"bounded", "kernel":get_kernel().NAME,
 "rows":profile["rows"], "columns":len(profile["columns"]),
 "seconds":time.perf_counter()-start,
 "peak_rss_raw":resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
 "peak_rss_unit":"KiB" if sys.platform.startswith("linux") else "bytes"}))
'''
        result = subprocess.run([sys.executable, "-c", child, str(path)], capture_output=True, text=True)
        print(result.stdout, end="", flush=True)
        print(json.dumps({"stage": "child_exit", "rows": rows, "exit_code": result.returncode}), flush=True)
        if result.returncode:
            # Do not print unfiltered exception text that could contain identity information.
            print(json.dumps({"stage": "error", "rows": rows,
                              "error_type": result.stderr.strip().splitlines()[-1].split(":")[0]
                              if result.stderr.strip() else "no_stderr"}), flush=True)


if __name__ == "__main__":
    for count in (5_000_000, 50_000_000):
        run(count)
