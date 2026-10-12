"""Record allocator policy A/B measurements with real generation and a machine label.

Run from the repository root with the installed development environment:
    python docs/plans/evidence/owner-notes/measure_allocator.py OUTPUT.json
No network or service is used. The parent never imports Shape (which would change
its allocator/THP state and contaminate the fresh child processes).
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import statistics
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "benchmarks" / "vs_refengine"))
from common import bench_lock, wait_for_quiet  # noqa: E402 — repository benchmark helpers

CHILD = r"""
import hashlib, json, resource, sys, time
started = time.perf_counter()
if sys.argv[1] == 'pyarrow-first':
    import pyarrow as pa
    import shape
else:
    import shape
    import pyarrow as pa
from shape.cli.generation import load_target
from shape.generation.engine import Engine
import_seconds = time.perf_counter() - started
schema = load_target('retail')
t0 = time.perf_counter()
tables = Engine(schema, scale='medium', seed=42).generate().tables
generation_seconds = time.perf_counter() - t0
rss_kib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
h = hashlib.sha256()
counts = {}
for name in sorted(tables):
    table = tables[name]
    counts[name] = table.num_rows
    h.update(name.encode())
    out = pa.BufferOutputStream()
    with pa.ipc.new_stream(out, table.schema) as writer:
        writer.write_table(table)
    h.update(out.getvalue().to_pybytes())
print(json.dumps({'generation_seconds': generation_seconds,
    'import_seconds': import_seconds, 'peak_generation_rss_kib': rss_kib,
    'sha256': h.hexdigest(), 'rows': counts,
    'arrow_default_backend': pa.default_memory_pool().backend_name}))
"""


def machine() -> dict:
    cpu = next(
        (
            line.split(":", 1)[1].strip()
            for line in Path("/proc/cpuinfo").read_text().splitlines()
            if line.startswith("model name")
        ),
        "not reported",
    )
    mem = next(
        line.split(":", 1)[1].strip()
        for line in Path("/proc/meminfo").read_text().splitlines()
        if line.startswith("MemTotal:")
    )
    schema = ROOT / "plugins/shape-domains/src/shape_domains/data/retail/schema.json"
    return {
        "label": "owner-notes-2-linux-x86_64-2026-10-12",
        "cpu_model": cpu,
        "logical_cpus": os.cpu_count(),
        "affinity_cpus": len(os.sched_getaffinity(0)),
        "visible_ram": mem,
        "cgroup_memory_max": Path("/sys/fs/cgroup/memory.max").read_text().strip(),
        "cgroup_cpu_max": Path("/sys/fs/cgroup/cpu.max").read_text().strip(),
        "os": platform.platform(),
        "kernel": platform.release(),
        "python": platform.python_version(),
        "pyarrow": importlib.metadata.version("pyarrow"),
        "shape": importlib.metadata.version("sqllocks-shape"),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "transparent_hugepage": Path("/sys/kernel/mm/transparent_hugepage/enabled")
        .read_text()
        .strip(),
        "retail_schema_sha256": hashlib.sha256(schema.read_bytes()).hexdigest(),
    }


def main() -> None:
    result = {
        "machine": machine(),
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "workload": "retail:medium",
        "seed": 42,
        "repeats": 3,
        "warmups_per_cell": 1,
        "runs": {},
    }
    env_base = dict(os.environ)
    env_base.pop("ARROW_DEFAULT_MEMORY_POOL", None)
    env_base.pop("SHAPE_MEMORY_POOL", None)
    cells = [
        (order, policy)
        for order in ("shape-first", "pyarrow-first")
        for policy in ("policy", "default")
    ]
    with bench_lock():
        for repeat in range(4):
            for order, policy in cells:
                load = wait_for_quiet()
                if load > 1.5:
                    raise RuntimeError(f"Quiet-load gate not met: {load}")
                env = dict(env_base)
                if policy == "default":
                    env["SHAPE_MEMORY_POOL"] = "default"
                done = subprocess.run(
                    [sys.executable, "-c", CHILD, order],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                run = json.loads(done.stdout.strip().splitlines()[-1])
                run["load_before"] = load
                key = f"{order}/{policy}"
                if repeat:
                    result["runs"].setdefault(key, []).append(run)
                print(f"{key} warmup={repeat == 0}: {run['generation_seconds']:.4f}s", flush=True)
    all_runs = [run for runs in result["runs"].values() for run in runs]
    assert len({run["sha256"] for run in all_runs}) == 1, (
        "Generated bytes differ between cells/runs"
    )
    assert all(run["rows"] == all_runs[0]["rows"] for run in all_runs), (
        "Generated row counts differ"
    )
    result["generated_bytes_identical"] = True
    result["summary"] = {
        key: {
            "median_generation_seconds": statistics.median(
                run["generation_seconds"] for run in runs
            ),
            "median_peak_generation_rss_kib": statistics.median(
                run["peak_generation_rss_kib"] for run in runs
            ),
        }
        for key, runs in result["runs"].items()
    }
    Path(sys.argv[1]).write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
