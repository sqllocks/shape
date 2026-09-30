"""CLI equivalence against Spindle (P1-11): ``profile capture``, ``profile diff`` and the
``profile --spindle-compat`` output, each compared with the pinned Spindle's.

    source scripts/env.sh && "$SHAPE_VENV/bin/python" \
        benchmarks/vs_spindle/profile_1to1/verify_cli.py [dataset ...]

* ``capture``: ``shape profile capture`` and ``spindle profile capture`` write byte-identical JSON
  for each dataset (default d1.csv d1.parquet d2.csv d2.parquet);
* ``diff``: ``shape profile diff`` and ``spindle profile diff`` print the same report (text and
  ``--json``) and exit with the same code, for captured D1/D2 against perturbed copies;
* ``profile``: for each CSV dataset, ``shape profile --spindle-compat -o`` is within T-22 of
  ``spindle_cli_profile.py`` (the PROF-CLI workload's Spindle side), field by field.

Exits 0 when everything matches, 1 on any difference, 2 when a dataset file is missing.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
from paths import PROFILE_DATA_DIR, SHAPE_PY, SPINDLE_PY  # noqa: E402

SPINDLE_CLI = Path(SPINDLE_PY).parent / "spindle"
SHAPE_CLI = Path(SHAPE_PY).parent / "shape"
DEFAULT = ["d1.csv", "d1.parquet", "d2.csv", "d2.parquet"]


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, check=False)


def fmt_of(name: str) -> list[str]:
    return ["--format", "parquet"] if name.endswith(".parquet") else []


def check_capture(datasets: list[str], work: Path, fails: list[str]) -> dict[str, Path]:
    captures: dict[str, Path] = {}
    for name in datasets:
        src = PROFILE_DATA_DIR / name
        sp, sh = work / f"sp_{name}.json", work / f"sh_{name}.json"
        a = run([SPINDLE_CLI, "profile", "capture", src, *fmt_of(name), "-o", sp])
        b = run([SHAPE_CLI, "profile", "capture", src, *fmt_of(name), "-o", sh])
        if a.returncode != 0 or b.returncode != 0:
            fails.append(f"capture {name}: exit spindle={a.returncode} shape={b.returncode}")
            continue
        same = sp.read_bytes() == sh.read_bytes()
        print(f"capture {name}: {'identical' if same else 'DIFFERENT'}", flush=True)
        if not same:
            fails.append(f"capture {name}: output differs")
        captures[name] = sh
    return captures


def perturb(cap: dict) -> dict:
    """A changed copy of a capture: shifted weights, one removed and one added distribution."""
    out = copy.deepcopy(cap)
    dists = out["distributions"]
    keys = sorted(dists)
    for k in keys[:2]:
        vals = dists[k]
        first = next(iter(vals))
        vals[first] = round(min(1.0, vals[first] + 0.2), 4)
        vals["__new__"] = 0.1
    if len(keys) > 2:
        del dists[keys[-1]]
    dists["t.brand_new"] = {"x": 0.5, "y": 0.5}
    return out


def check_diff(captures: dict[str, Path], work: Path, fails: list[str]) -> None:
    for name, path in captures.items():
        cap = json.loads(path.read_text(encoding="utf-8"))
        b = work / f"perturbed_{name}.json"
        b.write_text(json.dumps(perturb(cap), indent=2), encoding="utf-8")
        for extra in ([], ["--json"], ["--threshold", "0.0"], ["--threshold", "99", "--json"]):
            a = run([SPINDLE_CLI, "profile", "diff", path, b, *extra])
            s = run([SHAPE_CLI, "profile", "diff", path, b, *extra])
            same = a.returncode == s.returncode and a.stdout == s.stdout
            print(
                f"diff {name} {' '.join(extra) or '(text)'}: {'identical' if same else 'DIFFERENT'}"
            )
            if not same:
                fails.append(f"diff {name} {extra}: exit {a.returncode}/{s.returncode}")


def check_profile_cli(datasets: list[str], work: Path, fails: list[str]) -> None:
    import verify  # the field-by-field T-22 comparison

    for name in (d for d in datasets if d.endswith(".csv")):
        src = PROFILE_DATA_DIR / name
        sp, sh = work / f"spcli_{name}.json", work / f"shcli_{name}.json"
        a = run([SPINDLE_PY, HERE / "spindle_cli_profile.py", src, "-o", sp])
        b = run([SHAPE_CLI, "profile", src, "--spindle-compat", "-o", sh])
        if a.returncode != 0 or b.returncode != 0:
            fails.append(f"profile {name}: exit spindle={a.returncode} shape={b.returncode}")
            continue
        found: list[str] = []
        verify.check_table(json.loads(sp.read_text()), json.loads(sh.read_text()), name, {}, found)
        print(f"profile --spindle-compat {name}: {'T-22 ok' if not found else 'MISMATCH'}")
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
        captures = check_capture(datasets, work, fails)
        check_diff(captures, work, fails)
        check_profile_cli(datasets, work, fails)
    for f in fails:
        print("MISMATCH", f)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
