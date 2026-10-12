"""Require the scratch macOS interposer to trace known 200 MiB allocations."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    dylib = Path(sys.argv[1]).resolve()
    smoke = Path(sys.argv[2]).resolve()
    output = Path(sys.argv[3]).resolve()
    output.mkdir(parents=True, exist_ok=True)

    def run(label: str, command: list[str]) -> list[list[str]]:
        trace = output / f"{label}-malloc.csv"
        env = {
            **os.environ,
            "DYLD_INSERT_LIBRARIES": str(dylib),
            "RSS_TRACE_FILE": str(trace),
        }
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        (output / f"{label}-stdout.txt").write_text(result.stdout)
        (output / f"{label}-stderr.txt").write_text(result.stderr)
        print(f"{label}: exit={result.returncode}, stdout={result.stdout!r}", flush=True)
        if result.returncode or not trace.exists():
            print(f"{label} stderr: {result.stderr}", flush=True)
            raise AssertionError(f"{label}: child failed or interposer load marker absent")
        events = [line.split(",") for line in trace.read_text().splitlines()]
        print(f"{label}: trace_events={len(events)}", flush=True)
        if not events or events[0][0] != "L":
            print(f"{label} stderr: {result.stderr}", flush=True)
            raise AssertionError(f"{label}: interposer constructor marker absent")
        return events

    c_events = run("c", [str(smoke)])
    for api in ("malloc", "calloc", "posix_memalign"):
        matched = [
            event
            for event in c_events
            if event[0] == "A" and int(event[4]) >= 200 * 2**20 and event[11] == api
        ]
        print(f"C {api}: {len(matched)} traced 200 MiB allocations", flush=True)
        if not matched:
            raise AssertionError(f"C {api}: 200 MiB allocation missing from trace")

    python_code = (
        "import numpy as np\n"
        "a = np.empty(200 * 2**20, dtype=np.uint8)\n"
        "a[::4096] = 1\n"
        "print('numpy', a.nbytes, int(a[0]))\n"
    )
    python_events = run("numpy", [sys.executable, "-c", python_code])
    numpy_large = [
        event for event in python_events if event[0] == "A" and int(event[4]) >= 200 * 2**20
    ]
    print(f"NumPy: {len(numpy_large)} traced 200 MiB allocations", flush=True)
    if not numpy_large:
        raise AssertionError("NumPy: 200 MiB allocation missing from trace")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
