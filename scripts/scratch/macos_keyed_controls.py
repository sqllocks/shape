"""Prove the macOS keyed-state gate: the committed test passes, both growth controls fail."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TEST_FILE = Path("tests/streaming/test_keyed_state.py")
NODE = f"{TEST_FILE}::test_memory_does_not_grow_after_ten_million_events"
INIT = "done, at_checkpoint, peak_at_checkpoint, kept = 0, None, None, 0\n"
STEP = "    done += n\n"
CONTROLS = {
    # (a) retained growth proportional to events after the checkpoint: 8 bytes per event kept
    "retained": (
        INIT + "leak = []\n",
        STEP + "    if done > checkpoint:\n        leak.append(rng.random(n))\n",
    ),
    # (b) a transient proportional to events, freed before the end: 8 bytes per event so far
    "transient": (
        INIT,
        STEP + "    if done > checkpoint:\n        scratch = np.ones(done)\n        del scratch\n",
    ),
}


def run(label: str, output: Path) -> dict:
    log = output / f"{label}.log"
    with log.open("w") as stream:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-rP", "-m", "heavy", NODE],
            stdout=stream,
            stderr=subprocess.STDOUT,
        )
    text = log.read_text()
    print(text, flush=True)
    found = re.search(r"AssertionError: \((\d+), (\d+),", text)
    record = {
        "case": label,
        "pytest_exit": result.returncode,
        "assertion_failed": "AssertionError" in text,
        "values": [int(found[1]), int(found[2])] if found else None,
    }
    print("KEYED_CASE " + json.dumps(record), flush=True)
    return record


def main() -> int:
    output = Path(sys.argv[1]).resolve()
    output.mkdir(parents=True, exist_ok=True)
    if subprocess.check_output(["git", "status", "--porcelain"], text=True).strip():
        raise RuntimeError("checkout must be clean")
    original = TEST_FILE.read_text()
    cases = []
    try:
        for label in ("real-1", "real-2"):
            cases.append(run(label, output))
        for label, (init, step) in CONTROLS.items():
            assert original.count(INIT) == 1 and original.count(STEP) == 1
            TEST_FILE.write_text(original.replace(INIT, init).replace(STEP, step))
            cases.append(run(f"control-{label}", output))
            TEST_FILE.write_text(original)
    finally:
        TEST_FILE.write_text(original)
        status = subprocess.check_output(["git", "status", "--porcelain"], text=True)
        if status.strip():
            raise RuntimeError(f"checkout dirty after restoration: {status}")
    real = [c for c in cases if c["case"].startswith("real")]
    controls = [c for c in cases if c["case"].startswith("control")]
    passed = all(c["pytest_exit"] == 0 for c in real) and all(
        c["pytest_exit"] == 1 and c["assertion_failed"] for c in controls
    )
    print("KEYED_SUMMARY " + json.dumps({"cases": cases, "proof_passed": passed}), flush=True)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
