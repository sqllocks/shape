"""Execute the documented offline commands, checking exits and semantic results."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BLOCK = re.compile(r"<!-- exit: (\d+) -->\n```bash offline\n(.*?)\n```", re.S)


def verify() -> None:
    markdown = (HERE / "DEMO.md").read_text()
    blocks = BLOCK.findall(markdown)
    assert len(blocks) == markdown.count("```bash offline") == 7
    with tempfile.TemporaryDirectory(prefix="shape-friday-") as scratch:
        work = Path(scratch)
        env = {**os.environ, "TALK": str(HERE), "WORK": scratch, "UV_OFFLINE": "1"}
        # Preparation starts at the repo root; subsequent blocks share the scratch directory.
        for i, (expected, command) in enumerate(blocks):
            run = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
                cwd=ROOT if i == 0 else work,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=120,
            )
            print(f"block {i + 1}: exit {run.returncode} (expected {expected})")
            print(run.stdout)
            assert run.returncode == int(expected), command
        passed = json.loads((work / "pass.json").read_text())
        failed = json.loads((work / "fail.json").read_text())
        assert passed["passed"] is True
        assert failed["passed"] is False
        violations = {(v["column"], v["rule"]) for v in failed["violations"]}
        assert violations == {
            ("order_code", "regex"),
            ("order_date", "min"),
            ("status", "allowed_values"),
        }, failed
        for column, rule in sorted(violations):
            print(f"violation: {column} {rule}")
        drift = json.loads((work / "drift.json").read_text())
        kinds = {(c["column"], c["kind"]) for c in drift["changes"]}
        assert ("order_total", "mean_shift") in kinds, drift
        assert ("status", "new_categorical_values") in kinds, drift
        for column, kind in sorted(kinds):
            print(f"drift: {column} {kind}")
        generated = list((work / "dev").rglob("*.parquet"))
        assert generated and sum(pq.read_table(p).num_rows for p in generated) == 240
        assert (work / "dev.shape").exists() and (work / "day1.html").exists()
        json.loads((work / "plan.json").read_text())
        regenerated = work / "regenerated"
        subprocess.run(
            [
                "uv",
                "run",
                "--locked",
                "python",
                str(HERE / "data/generate_samples.py"),
                "--out",
                str(regenerated),
            ],
            cwd=ROOT,
            env=env,
            check=True,
            timeout=120,
        )
        for source in (HERE / "data").glob("*.parquet"):
            assert pq.read_table(source).equals(pq.read_table(regenerated / source.name))
        assert sum(p.stat().st_size for p in (HERE / "data").glob("*.parquet")) < 2_000_000
    print("OFFLINE DEMO VERIFIED: exits, violations, drift, generation, seeded fixtures")


if __name__ == "__main__":
    verify()
