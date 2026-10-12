"""Measure the real test and one temporary growth control on a macOS runner."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TEST = "tests/profile/test_engine.py::test_bounded_mode_memory_does_not_grow_with_rows"
ANCHOR = b"        \"profile(sys.argv[1], mode='bounded')\\n\"\n"
INJECTION = (
    b"        \"held = bytearray(int(__import__('pathlib').Path(sys.argv[1]).stem[1:]) * 8)\\n\"\n"
    # Touch every page: lazy zero pages must not make this a virtual-only allocation.
    b"        \"held[::4096] = b'x' * ((len(held) + 4095) // 4096)\\n\"\n"
)


def run_case(label: str, output: Path) -> dict:
    log = output / f"{label}.log"
    with log.open("w") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rP", TEST],
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    text = log.read_text()
    print(text, flush=True)
    peaks = {}
    diagnostics = {}
    for line in text.splitlines():
        match = re.fullmatch(r"(\d+) rows: peak physical footprint (\d+) bytes; (.+)", line)
        if match:
            rows, peak, stderr = match.groups()
            peaks[int(rows)] = int(peak)
            diagnostics[rows] = json.loads(stderr)
    (output / f"{label}-stderr.json").write_text(json.dumps(diagnostics, indent=2) + "\n")
    record = {"case": label, "pytest_exit": result.returncode, "footprints": peaks}
    (output / f"{label}-result.json").write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps(record), flush=True)
    return {**record, "assertion_failed": "assert abs(peaks[48_000_000]" in text}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("requires macOS; Linux RSS is not physical-footprint evidence")
    repo = Path(__file__).resolve().parents[2]
    if Path.cwd().resolve() != repo:
        parser.error("run from the repository root")
    output = args.output.resolve()
    if output == repo or repo in output.parents:
        parser.error("output must be outside the checkout")
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("checkout must be clean before the control")
    output.mkdir(parents=True, exist_ok=True)
    source = repo / "tests/profile/test_engine.py"
    original = source.read_bytes()
    assert original.count(ANCHOR) == 1
    assert b"return mac_usage(os.getpid())['ri_lifetime_max_phys_footprint']" in original
    try:
        real = run_case("real", output)
        source.write_bytes(original.replace(ANCHOR, INJECTION + ANCHOR))
        control = run_case("control", output)
    finally:
        source.write_bytes(original)
        assert source.read_bytes() == original
        status = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        (output / "checkout-status.txt").write_text(status)
        if status.strip():
            raise RuntimeError(f"checkout is dirty after restoration: {status}")
        print("Source restored byte-identical; checkout clean.", flush=True)
    valid = True
    for case in (real, control):
        peaks = case["footprints"]
        if set(peaks) != {24_000_000, 48_000_000} or peaks[24_000_000] <= 50 * 2**20:
            valid = False
    peaks = control["footprints"]
    detected_growth = (
        valid and abs(peaks[48_000_000] - peaks[24_000_000]) / peaks[24_000_000] >= 0.10
    )
    passed = (
        real["pytest_exit"] == 0
        and control["pytest_exit"] == 1
        and control["assertion_failed"]
        and detected_growth
    )
    summary = {"real": real, "control": control, "proof_passed": passed}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
