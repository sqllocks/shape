"""``shape fidelity --tier N`` and ``shape drift``: the fidelity tiers on the command line.

Kept apart from :mod:`shape.cli.main` (which only wires the arguments and the two entry points in)
and imported only when one of these runs, so no numerical library loads at start-up.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def add_fidelity_arguments(parser: argparse.ArgumentParser) -> None:
    """The tier options of ``shape fidelity``."""
    g = parser.add_argument_group(
        "tiers",
        "--tier 1: mixture fits, conditional profiles, adversarial AUC, temporal profiles and "
        "periodicity (the adversarial and mixture parts need `pip install "
        "'sqllocks-shape[advanced]'`); --tier 2: format preservation, string similarity, "
        "cardinality and anomaly-rate checks; --tier 3: the Chow-Liu dependency tree of each side "
        "and how much of its structure the synthetic data keeps (experimental).",
    )
    g.add_argument("--tier", type=int, choices=(1, 2, 3), help="run this fidelity tier instead")
    g.add_argument(
        "--max-auc",
        type=float,
        default=0.75,
        help="tier 1: the adversarial AUC must stay below this (default 0.75)",
    )
    g.add_argument(
        "--min-pass-rate",
        type=float,
        default=1.0,
        help="tier 2: share of the checks that must pass (default 1.0)",
    )
    g.add_argument(
        "--expected-anomaly-rate",
        type=float,
        help="tier 2: the expected share of rows flagged in _shape_is_anomaly (default 0)",
    )
    g.add_argument(
        "--min-edge-overlap",
        type=float,
        help="tier 3: least overlap of the two dependency trees' edges (default: no gate)",
    )


def add_drift_parser(sub: Any) -> None:
    dr = sub.add_parser(
        "drift",
        help="test current data for drift against reference data",
        description=(
            "Compare CURRENT with REFERENCE (a file, or a directory of one file per table), "
            "column by column. With --psi each column is scored by its population stability "
            "index alone (no SciPy needed); without it numbers get a KS test and everything else "
            "a chi-squared test, with the PSI as a second signal (needs SciPy). Exit 0 when no "
            "column drifted, 1 when one did, 2 for bad input."
        ),
    )
    dr.add_argument("reference", metavar="REFERENCE")
    dr.add_argument("current", metavar="CURRENT")
    dr.add_argument("--psi", action="store_true", help="population stability index only")
    dr.add_argument(
        "--threshold", type=float, default=0.2, help="PSI above this is drift (default 0.2)"
    )
    dr.add_argument(
        "--pvalue", type=float, default=0.05, help="p-value below this is drift (default 0.05)"
    )
    dr.add_argument("--input-format", default="auto", choices=("auto", "csv", "parquet", "jsonl"))
    dr.add_argument("-o", "--output", metavar="REPORT", help="also write the report (JSON) here")


def _pairs(a: argparse.Namespace, second: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """The reference tables and the matching other tables (a single file against a single file
    compares whatever they are called), and the names of reference tables the other side lacks."""
    from shape.quality import load_tables

    real = load_tables(a.reference, a.input_format)
    other = load_tables(getattr(a, second), a.input_format)
    if not real:
        raise ValueError(f"no data files found in {a.reference}")
    if len(real) == 1 and len(other) == 1:
        other = {next(iter(real)): next(iter(other.values()))}
    missing = [t for t in real if t not in other]
    return real, other, missing


def _emit(report: dict[str, Any], outs: Any, text: str | None = None) -> None:
    """Write the JSON report to every file in ``outs`` (a path, a list of them or None) and
    print it (or ``text``)."""
    raw = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    for out in [outs] if isinstance(outs, str) else outs or ():
        Path(out).write_text(raw, encoding="utf-8")
    sys.stdout.write(text if text is not None else raw)


def run_fidelity(a: argparse.Namespace) -> int:
    """``shape fidelity REFERENCE SYNTHETIC --tier N``: 0 when the tier's gates hold, 1 when not."""
    if a.format not in ("json", "text"):
        raise ValueError("a tier report is printed as json or text (--format)")
    for out in a.output:
        if Path(out).suffix.lower() != ".json":
            raise ValueError(f"a tier report is written as JSON: name it .json, not {out}")
    real, synth, missing = _pairs(a, "csv")
    report: dict[str, Any] = {"tier": a.tier, "tables": {}, "notes": []}
    passed = not missing
    for name in missing:
        report["notes"].append(f"table {name!r} is missing from the synthetic data")
    text: list[str] = []
    for name, r in real.items():
        s = synth.get(name)
        if s is None:
            continue
        entry, ok, lines = _run_tier(a, name, r, s)
        report["tables"][name] = entry
        passed = passed and ok
        text += lines
        report["notes"] += [f"{name}: {n}" for n in entry.get("notes", [])]
    report["passed"] = passed
    for note in report["notes"]:
        print(f"shape: note: {note}", file=sys.stderr)
    _emit(report, a.output, "\n".join(text) + "\n" if a.format == "text" else None)
    return 0 if passed else 1


def _run_tier(
    a: argparse.Namespace, name: str, real: Any, synth: Any
) -> tuple[dict[str, Any], bool, list[str]]:
    if a.tier == 1:
        from shape.fidelity.tier1 import Tier1Profiler

        profile = Tier1Profiler(adversarial_threshold=a.max_auc).profile_pair(real, synth, name)
        adv = profile.adversarial
        ok = adv is None or adv.passed
        line = f"{name}: adversarial AUC " + (f"{adv.auc_roc:.3f}" if adv else "not computed")
        return profile.to_dict(), ok, [line]
    if a.tier == 2:
        from shape.fidelity.tier2 import run_tier2

        fractions = (
            {"expected": a.expected_anomaly_rate} if a.expected_anomaly_rate is not None else None
        )
        t2 = run_tier2(real, synth, fractions)
        return t2.to_dict(), t2.passing_rate() >= a.min_pass_rate, [f"{name}\n{t2.summary()}"]
    from shape.fidelity.tier3 import ChowLiuTree, compare_trees

    tree = ChowLiuTree()
    rt, st = tree.fit(real), tree.fit(synth)
    cmp = compare_trees(rt, st)
    ok = a.min_edge_overlap is None or cmp["edge_overlap"] >= a.min_edge_overlap
    entry = {"reference": rt.to_dict(), "synthetic": st.to_dict(), "comparison": cmp}
    return entry, ok, [f"{name}: edge overlap {cmp['edge_overlap']:.3f}"]


def run_drift(a: argparse.Namespace) -> int:
    """``shape drift REFERENCE CURRENT``: 0 no drift, 1 drift, 2 bad input (raised)."""
    from shape.fidelity.tier3 import DriftMonitor, psi_report

    real, cur, missing = _pairs(a, "current")
    report: dict[str, Any] = {"method": "psi" if a.psi else "ks+chi2+psi", "tables": {}}
    drifted = bool(missing)
    report["missing_tables"] = missing
    for name, r in real.items():
        c = cur.get(name)
        if c is None:
            continue
        if a.psi:
            res = psi_report(r, c, threshold=a.threshold)
        else:
            res = DriftMonitor(pvalue_threshold=a.pvalue, psi_threshold=a.threshold).compare(r, c)
        report["tables"][name] = res.to_dict()
        drifted = drifted or bool(res.drifted_columns)
    report["drifted"] = drifted
    _emit(report, a.output)
    return 1 if drifted else 0
