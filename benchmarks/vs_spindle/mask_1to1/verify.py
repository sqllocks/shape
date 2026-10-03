"""Shape's ``mask`` against the baseline's on the same data (internal harness, P6-03).

    source scripts/env.sh
    "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/mask_1to1/verify.py [--rows N] [CASE ...]

Cases: ``d2`` (the 1M-row profiling dataset, ``$PROFILE_DATA_DIR/d2.csv``: the acceptance case;
``--rows N`` keeps its first N rows) and ``cat`` (a small generated table of categorical personal
data). Both tools run as their own commands on the same CSV; then, for each tool, three checks
(``checks.py``):

1. the same masked columns (the columns whose values changed; for each tool this must also equal
   the columns the tool reports);
2. format preserved (every masked value has the format of the column's original values);
3. no original value remaining (no cell keeps its value; no replacement in an identifier column
   is an original value; no original value appears in another column).

Shape must pass 2 and 3 everywhere, with no allowance. The baseline's differences are a narrow,
named allow-list (``ALLOWED``): a difference that is not listed fails the run, and so does a listed
one that did not occur. Then a negative control: three corrupted copies of Shape's output (a kept
original value, a broken format, an unmasked column) must each be caught by the matching check.
Exit 0 only if everything holds. Results: ``$BENCH_OUT_DIR/mask_1to1/result.json``.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import checks  # noqa: E402
from paths import BENCH_OUT_DIR, PROFILE_DATA_DIR, SHAPE_PY, SPINDLE_PY  # noqa: E402

FAKER_PIN = "faker==40.40.0"  # the baseline's optional masking dependency (not in its core set)
SEED = 42

# The deliberate differences from the baseline: owner decision 2026-10-01 (fix defects that harm
# user trust, record each in a narrow allow-list). Keys: case -> kind -> {column: reason}.
ALLOWED: dict[str, dict[str, dict[str, str]]] = {
    "d2": {
        "masked_only_by_baseline": {
            "last_login": (
                "MASK-A1: a column whose name contains `login` is masked as a user name whatever "
                "its values are; the baseline replaces a timestamp column with user names. Shape "
                "masks a name-matched column only when its type and values can hold the type."
            ),
        },
        "baseline_format_broken": {
            "phone": (
                "MASK-A2: the baseline writes any phone layout (`+1-210-343-3218x1960`, "
                "`538.990.8386`) over `(387) 306-3453`; Shape keeps the layout."
            ),
            "zip4": (
                "MASK-A2: the baseline writes a 5-digit code over a ZIP+4 (`68307-9188`); Shape "
                "keeps the layout."
            ),
            "ip_address": (
                "MASK-A3: `ip_address` matches the `address` phrase first, so the baseline "
                "writes street addresses over IPv4 addresses; Shape picks the longest matching "
                "phrase (`ip_address`) and writes IPv4 addresses."
            ),
            "last_login": "MASK-A1 (the timestamps became user names).",
        },
        "baseline_keeps_original": {},
        # MASK-A6 (lead decision 2026-10-02): Shape writes every replacement e-mail at a reserved
        # example domain (RFC 2606), as generation does (owner issue 11); `judge` checks that with
        # no allowance. The pinned baseline's faker already draws example.com/.org/.net, so this
        # is not a difference from it; a column listed here would be one where the baseline
        # writes other domains, and the probe fails if the list and the output disagree.
        "baseline_real_email_domains": {},
    },
    "cat": {
        "masked_only_by_baseline": {},
        "baseline_format_broken": {
            "first_name": (
                "MASK-A4: the `name` phrase is tried before `first_name`, so the baseline "
                "writes full names into a first-name column; Shape picks the longest phrase."
            ),
        },
        "baseline_keeps_original": {
            "state": (
                "MASK-A5: replacements are drawn from a pool without excluding the original "
                "value, so about 1 cell in 50 keeps its state; Shape draws until it differs."
            ),
        },
        "baseline_real_email_domains": {},  # MASK-A6, as for d2
    },
}

RESERVED_DOMAINS = ("example.com", "example.org", "example.net")


# ---- data ----------------------------------------------------------------------------------


def read_csv(path: Path) -> dict[str, list[str | None]]:
    """All values as text; an empty field is a null."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        cols: dict[str, list[str | None]] = {h: [] for h in header}
        for row in reader:
            for h, v in zip(header, row, strict=True):
                cols[h].append(v if v != "" else None)
    return cols


def write_csv(path: Path, cols: dict[str, list[str | None]]) -> None:
    n = len(next(iter(cols.values())))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(list(cols))
        for i in range(n):
            w.writerow(["" if cols[c][i] is None else cols[c][i] for c in cols])


def make_cat(path: Path, rows: int = 5000) -> None:
    import numpy as np

    rng = np.random.default_rng(7)
    states = (
        "CA TX NY FL IL PA OH GA NC MI NJ VA WA AZ MA TN IN MO MD WI CO MN SC AL LA KY OR OK CT "
        "UT IA NV AR MS KS NM NE ID WV HI NH ME MT RI DE SD ND AK VT WY"
    ).split()
    names = (
        "James Mary John Patricia Robert Jennifer Michael Linda William Elizabeth David Barbara "
        "Richard Susan Joseph Jessica Thomas Sarah Charles Karen Daniel Nancy Matthew Lisa Anthony "
        "Betty Mark Dorothy Paul Sandra Steven Ashley Andrew Kimberly Kenneth Donna Joshua Emily "
        "Kevin Michelle Brian Carol George Amanda Edward Melissa Ronald Deborah Timothy Stephanie"
    ).split()
    cities = [
        "New York",
        "Los Angeles",
        "Chicago",
        "Houston",
        "Phoenix",
        "Philadelphia",
        "San Antonio",
        "San Diego",
        "Dallas",
        "San Jose",
        "Austin",
        "Jacksonville",
        "Fort Worth",
        "Columbus",
        "Charlotte",
        "Indianapolis",
        "Seattle",
        "Denver",
        "Boston",
        "Nashville",
        "Portland",
        "Las Vegas",
        "Detroit",
        "Memphis",
        "Baltimore",
        "Milwaukee",
        "Albuquerque",
        "Tucson",
        "Fresno",
        "Sacramento",
        "Atlanta",
        "Omaha",
        "Raleigh",
        "Miami",
        "Oakland",
        "Tulsa",
    ]
    cols: dict[str, list[str | None]] = {
        "id": [str(i + 1) for i in range(rows)],
        "state": [states[i] for i in rng.integers(0, len(states), rows)],
        "first_name": [names[i] for i in rng.integers(0, len(names), rows)],
        "city": [cities[i] for i in rng.integers(0, len(cities), rows)],
        "email": [f"person{i}@corp-{i % 97}.example" for i in range(rows)],
        "note": [f"row {i}" for i in range(rows)],
    }
    for c in ("state", "city"):  # some nulls, to check they stay
        for i in rng.integers(0, rows, rows // 20):
            cols[c][int(i)] = None
    write_csv(path, cols)


# ---- the two tools -------------------------------------------------------------------------


def ensure_faker() -> str:
    probe = subprocess.run(
        [str(SPINDLE_PY), "-c", "import faker; print(faker.VERSION)"],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        subprocess.run([str(SPINDLE_PY), "-m", "pip", "install", "-q", FAKER_PIN], check=True)
        probe = subprocess.run(
            [str(SPINDLE_PY), "-c", "import faker; print(faker.VERSION)"],
            capture_output=True,
            text=True,
            check=True,
        )
    return probe.stdout.strip()


def run_baseline(src: Path, out: Path) -> tuple[set[str], float]:
    spindle = SPINDLE_PY.with_name("spindle")
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(spindle), "mask", str(src), "-o", str(out), "--seed", str(SEED)],
        capture_output=True,
        text=True,
        check=True,
    )
    seconds = time.perf_counter() - t0
    reported: set[str] = set()
    for line in proc.stdout.splitlines():
        m = re.match(r"\s*Masked:\s*(.*)", line)
        if m:
            reported |= {c.strip() for c in m.group(1).split(",") if c.strip()}
    return reported, seconds


def run_shape(src: Path, out: Path) -> tuple[set[str], float]:
    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(SHAPE_PY.with_name("shape")), "mask", str(src), "-o", str(out), "--seed", str(SEED)]
        + ["--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    seconds = time.perf_counter() - t0
    doc = json.loads(proc.stdout)
    return {c for cols in doc["columns_masked"].values() for c in cols}, seconds


# ---- evaluation ----------------------------------------------------------------------------


def evaluate(case: str, original: dict, masked: dict, reported: set[str], tool: str) -> dict:
    changed = checks.changed_columns(original, masked)
    structure = checks.check_structure(original, masked)
    return {
        "tool": tool,
        "changed": sorted(changed),
        "reported": sorted(reported),
        "report_matches_values": changed == reported,
        "structure": structure,
        "format": checks.check_format(original, masked, changed),
        "no_original": checks.check_no_original(original, masked, changed),
        "email_reserved": _email_domain_probe(original, masked, changed),
    }


def _email_domain_probe(original: dict, masked: dict, changed: set[str]) -> dict[str, bool]:
    """For each masked e-mail column: does every replacement sit at a reserved example domain?"""
    out: dict[str, bool] = {}
    for c in sorted(changed):
        values = [v for v in masked[c] if v is not None]
        if values and all(checks.EMAIL.match(v) for v in values):
            out[c] = all(v.rsplit("@", 1)[1].lower() in RESERVED_DOMAINS for v in values)
    return out


def _failing(problems: list[str]) -> set[str]:
    return {p.split(":", 1)[0] for p in problems}


def judge(case: str, shape: dict, base: dict) -> list[str]:
    """The failures of one case: empty means the case passes."""
    fails: list[str] = []
    allow = ALLOWED[case]
    for key in ("structure", "format", "no_original"):
        if shape[key]:
            fails += [f"shape {key}: {p}" for p in shape[key]]
    if not shape["report_matches_values"]:
        fails.append(f"shape reports {shape['reported']} but changed {shape['changed']}")
    if not base["report_matches_values"]:
        fails.append(f"baseline reports {base['reported']} but changed {base['changed']}")
    if base["structure"]:
        fails += [f"baseline structure: {p}" for p in base["structure"]]

    # Shape's replacement e-mails are all reserved example addresses, with no allowance (MASK-A6);
    # the baseline's differ only where the allow-list says so, and the list may not go stale.
    unreserved = {c for c, ok in shape["email_reserved"].items() if not ok}
    if unreserved:
        fails.append(
            f"shape e-mail replacements outside the reserved domains: {sorted(unreserved)}"
        )
    base_real = {c for c, ok in base["email_reserved"].items() if not ok}
    if base_real != set(allow["baseline_real_email_domains"]):
        fails.append(
            f"baseline e-mail columns with non-reserved domains {sorted(base_real)} != "
            f"allow-list {sorted(allow['baseline_real_email_domains'])}"
        )
    # 1. the same masked columns, up to the named differences
    only_base = set(base["changed"]) - set(shape["changed"])
    only_shape = set(shape["changed"]) - set(base["changed"])
    if only_shape:
        fails.append(f"Shape masks columns the baseline does not: {sorted(only_shape)}")
    if only_base != set(allow["masked_only_by_baseline"]):
        fails.append(
            f"columns masked only by the baseline {sorted(only_base)} != allow-list "
            f"{sorted(allow['masked_only_by_baseline'])}"
        )
    # 2 and 3 for the baseline: every defect must be a named one, and every named one must occur
    bad_format = _failing(base["format"])
    if bad_format != set(allow["baseline_format_broken"]):
        fails.append(
            f"baseline format failures {sorted(bad_format)} != allow-list "
            f"{sorted(allow['baseline_format_broken'])}"
        )
    kept = {p.split(":", 1)[0] for p in base["no_original"] if "keep their original" in p}
    other = [p for p in base["no_original"] if "keep their original" not in p]
    if kept != set(allow["baseline_keeps_original"]):
        fails.append(
            f"baseline columns that keep original values {sorted(kept)} != allow-list "
            f"{sorted(allow['baseline_keeps_original'])}"
        )
    if other:
        fails += [f"baseline (unlisted): {p}" for p in other]
    return fails


def negative_controls(original: dict, shape_out: dict, expected: set[str]) -> dict[str, bool]:
    """Corrupted copies of Shape's output; each must be caught by the matching check."""

    def copy() -> dict:
        return {c: list(v) for c, v in shape_out.items()}

    results: dict[str, bool] = {}
    cols = sorted(expected)
    email = next((c for c in cols if "email" in c), cols[0])
    phone = next((c for c in cols if "phone" in c), cols[-1])

    leaked = copy()  # one original value left in place
    row = next(i for i, v in enumerate(original[email]) if v is not None)
    leaked[email][row] = original[email][row]
    results["a kept original value is caught"] = bool(
        checks.check_no_original(original, leaked, expected)
    )

    reused = copy()  # a replacement that is another row's original value
    rows = [i for i, v in enumerate(original[email]) if v is not None][:2]
    reused[email][rows[0]] = original[email][rows[1]]
    results["an original value reused in another row is caught"] = bool(
        checks.check_no_original(original, reused, expected)
    )

    broken = copy()  # a format that is not the original's
    broken[phone] = [None if v is None else "@@ 12345 @@" for v in broken[phone]]
    results["a broken format is caught"] = bool(checks.check_format(original, broken, expected))

    unmasked = copy()  # a column that should have been masked, left as it was
    unmasked[phone] = list(original[phone])
    results["an unmasked column is caught"] = bool(
        checks.check_columns(original, unmasked, expected)
    )
    return results


def run_case(case: str, rows: int | None, work: Path) -> dict:
    case_dir = work / case
    case_dir.mkdir(parents=True, exist_ok=True)
    if case == "d2":
        source = PROFILE_DATA_DIR / "d2.csv"
        if not source.exists():
            raise SystemExit(f"{source} is missing: run profile_1to1/datasets.py D2 first")
        src = case_dir / "d2.csv"
        with open(source, encoding="utf-8") as fin, open(src, "w", encoding="utf-8") as fout:
            for i, line in enumerate(fin):
                if rows is not None and i > rows:
                    break
                fout.write(line)
    else:
        src = case_dir / "cat.csv"
        make_cat(src)
    original = read_csv(src)
    reported_base, t_base = run_baseline(src, case_dir / "baseline")
    reported_shape, t_shape = run_shape(src, case_dir / "shape")
    base_out = read_csv(case_dir / "baseline" / src.name)
    shape_out = read_csv(case_dir / "shape" / src.name)
    shape = evaluate(case, original, shape_out, reported_shape, "shape")
    base = evaluate(case, original, base_out, reported_base, "baseline")
    fails = judge(case, shape, base)
    controls = negative_controls(original, shape_out, set(shape["changed"]))
    fails += [f"negative control not caught: {k}" for k, ok in controls.items() if not ok]
    return {
        "case": case,
        "rows": len(next(iter(original.values()))),
        "seconds": {"baseline": round(t_base, 2), "shape": round(t_shape, 2)},
        "shape": shape,
        "baseline": base,
        "allowed": ALLOWED[case],
        "negative_controls": controls,
        "failures": fails,
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cases", nargs="*", help="d2 and/or cat (default: both)")
    ap.add_argument("--rows", type=int, help="keep only the first N rows of d2")
    a = ap.parse_args(argv)
    if set(a.cases) - {"d2", "cat"}:
        ap.error("cases are d2 and cat")
    faker = ensure_faker()
    work = BENCH_OUT_DIR / "mask_1to1"
    work.mkdir(parents=True, exist_ok=True)
    results = []
    for case in a.cases or ["d2", "cat"]:
        r = run_case(case, a.rows, work)
        results.append(r)
        print(
            f"{case}: {r['rows']:,} rows; baseline {r['seconds']['baseline']} s, "
            f"Shape {r['seconds']['shape']} s"
        )
        print(f"  Shape masked   {r['shape']['changed']}")
        print(f"  baseline masked {r['baseline']['changed']}")
        for kind, entries in r["allowed"].items():
            for col, why in entries.items():
                print(f"  allowed [{kind}] {col}: {why}")
        for name, ok in r["negative_controls"].items():
            print(f"  control: {name}: {'ok' if ok else 'NOT CAUGHT'}")
        for f in r["failures"]:
            print(f"  FAIL {f}")
        print(f"  {'PASS' if not r['failures'] else 'FAIL'}")
    (work / "result.json").write_text(
        json.dumps({"faker": faker, "seed": SEED, "cases": results}, indent=2) + "\n"
    )
    return 1 if any(r["failures"] for r in results) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
