"""CLI equivalence against the pinned baseline (P1-11, P1-14): the profile ``shape profile``
writes, compared with the baseline CLI's, through ``adapter.py``.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_refengine/profile_1to1/verify_cli.py [dataset ...]

* ``profile``: for each CSV dataset, the ``.shape`` file ``shape profile -o`` writes, mapped by
  ``adapter.py``, is within T-22 of ``refengine_cli_profile.py`` (the PROF-CLI workload's baseline
  side), field by field.

Exits 0 when everything matches, 1 on any difference, 2 when a dataset file is missing.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from paths import PROFILE_DATA_DIR, REFENGINE_PY, SHAPE_PY  # noqa: E402

SHAPE_CLI = Path(SHAPE_PY).parent / "shape"
DEFAULT = ["d1.csv", "d1.parquet", "d2.csv", "d2.parquet"]


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=False)


def check_profile_cli(datasets: list[str], work: Path, fails: list[str]) -> None:
    import verify  # the field-by-field T-22 comparison
    from adapter import profile_json

    for name in (d for d in datasets if d.endswith(".csv")):
        src = PROFILE_DATA_DIR / name
        sp, sh = work / f"spcli_{name}.json", work / f"shcli_{name}.shape"
        a = run([REFENGINE_PY, HERE / "refengine_cli_profile.py", src, "-o", sp])
        # T-22 compares the profile with the baseline's, which keeps real values: ask for them
        b = run([SHAPE_CLI, "profile", src, "-o", sh, "--capture", "full"])
        if a.returncode != 0 or b.returncode != 0:
            fails.append(f"profile {name}: exit refengine={a.returncode} shape={b.returncode}")
            continue
        found: list[str] = []
        product = profile_json(sh)
        # the in-process harness's identifier rule (ISS2-bugs #46): only its allow-listed columns
        baseline = verify.identifier_rule_baseline(name, json.loads(sp.read_text()), product)
        verify.check_table(baseline, product, name, {}, found)
        print(f"profile {name}: {'T-22 ok' if not found else 'MISMATCH'}")
        fails += [f"profile {name}: {line}" for line in found[:20]]


def main(argv: list[str]) -> int:
    datasets = [a for a in argv if not a.startswith("--")] or DEFAULT
    missing = [d for d in datasets if not (PROFILE_DATA_DIR / d).exists()]
    if missing:
        print(
            f"missing datasets under {PROFILE_DATA_DIR}: {missing}; run datasets.py",
            file=sys.stderr,
        )
        return 2
    fails: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        check_profile_cli(datasets, work, fails)
    for f in fails:
        print("MISMATCH", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
