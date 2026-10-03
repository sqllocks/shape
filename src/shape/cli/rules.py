"""``shape rules mutate|backtest``: score a contract against planted faults and against history.

``mutate`` plants the corruptions of ``shape chaos`` one at a time and reports which rules catch
them; ``backtest`` replays a contract over every committed version of a registry name. Exit codes:
0 after a report, 1 when ``--min-score`` is not met or ``--fail-on-miss`` finds a missed incident,
2 for unusable input. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def add_arguments(sub: Any) -> None:
    """Register ``rules`` on the subparsers."""
    top = sub.add_parser(
        "rules",
        help="test a contract: mutation testing against planted faults, backtesting on history",
        description="`rules mutate` plants known faults in the data and reports which rules of a "
        "contract catch them; `rules backtest` replays a contract over a registry's history. "
        "See docs/RULES_TESTING.md.",
    )
    cmds = top.add_subparsers(dest="rules_cmd", required=True)

    m = cmds.add_parser(
        "mutate",
        help="plant known faults and report which rules catch them",
        description="Each mutant is one corruption of `shape chaos` on one table and column, "
        "profiled in memory and checked against the contract (and, with --diff or a `drift` "
        "section in the contract, compared with the unmutated profile). A mutant is killed when a "
        "rule or a drift change fires. The same seed gives the same report.",
    )
    m.add_argument("data", metavar="DATA", help="a data file or a folder of them, one table each")
    m.add_argument("contract", metavar="CONTRACT.json")
    m.add_argument("--plan", metavar="PLAN.json", help="the mutants to run (default: every one)")
    m.add_argument("--seed", type=int, default=0, help="default: 0")
    m.add_argument("--rate", type=float, default=0.05, help="share of rows changed (default 0.05)")
    m.add_argument("--diff", action="store_true", help="also compare each mutant with the original")
    m.add_argument(
        "--min-score", type=float, metavar="S", help="exit 1 when the mutation score is below S"
    )
    m.add_argument("-o", "--output", metavar="REPORT.json", help="write the report here")
    m.add_argument("--json", action="store_true", help="print the report as JSON")

    b = cmds.add_parser(
        "backtest",
        help="replay a contract over every committed version of a registry name",
        description="Reads each version as `shape check` does (oldest first, by business date, "
        "else commit date). A rule the stored form cannot evaluate is `not measured`, never a "
        "pass. With --window week|month the versions of a window are merged first.",
    )
    b.add_argument("registry", metavar="REGISTRY", help="the registry folder")
    b.add_argument("name", metavar="NAME", help="the registry name")
    b.add_argument("contract", metavar="CONTRACT.json")
    b.add_argument("--since", metavar="DATE", help="first date, inclusive (YYYY-MM-DD)")
    b.add_argument("--until", metavar="DATE", help="last date, inclusive (YYYY-MM-DD)")
    b.add_argument("--window", choices=("day", "week", "month"), default="day", help="default: day")
    b.add_argument("--incidents", metavar="FILE", help="known incidents (shape-incidents)")
    b.add_argument("--fail-on-miss", action="store_true", help="exit 1 when an incident is missed")
    b.add_argument("--compare", metavar="OLD_CONTRACT.json", help="also run an older contract")
    b.add_argument("-o", "--output", metavar="REPORT.json", help="write the report here")
    b.add_argument("--json", action="store_true", help="print the report as JSON")


def _emit(report: dict[str, Any], a: argparse.Namespace, text: str) -> None:
    if a.output:
        Path(a.output).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if a.json:
        json.dump(report, sys.stdout, indent=2)
        print()
    else:
        print(text, end="")


def _pct(score: float | None) -> str:
    return "n/a" if score is None else f"{score * 100:.1f}%"


def mutation_text(report: dict[str, Any]) -> str:
    """The text of a mutation report: the surviving mutants first."""
    overall = report["score"]["overall"]
    lines = [
        f"mutation score {_pct(overall['score'])} "
        f"({overall['killed']} of {overall['applicable']} applicable mutants killed; "
        f"seed {report['seed']}, rate {report['rate']})"
    ]
    mutants = report["mutants"]
    if report["baseline_failed_rules"]:
        lines.append(
            "these rules already fail on the unmutated data, so they cannot kill a mutant: "
            + ", ".join(report["baseline_failed_rules"])
        )

    def section(title: str, status: str) -> None:
        picked = [m for m in mutants if m["status"] == status]
        if not picked:
            return
        lines.append("")
        lines.append(f"{title} ({len(picked)})")
        for m in picked:
            by = f"  by {', '.join(m['killed_by'])}" if m["killed_by"] else ""
            lines.append(f"  {m['id']}  [{m['cells_changed']} cells]{by}")

    section("SURVIVED: no rule caught these", "survived")
    section("killed", "killed")
    section("not applicable (nothing changed; outside the score)", "not_applicable")
    for title, key in (("by kind", "by_kind"), ("by table", "by_table")):
        lines.append("")
        lines.append(title)
        for name, s in report["score"][key].items():
            lines.append(f"  {name:<20} {_pct(s['score']):>7}  ({s['killed']}/{s['applicable']})")
    if report["rules_killed_none"]:
        lines.append("")
        lines.append("rules that killed no mutant")
        lines += [f"  {r}" for r in report["rules_killed_none"]]
    return "\n".join(lines) + "\n"


def backtest_text(report: dict[str, Any]) -> str:
    """The text of a backtest report."""
    s = report["summary"]
    lines = [
        f"{report['name']}: {s['entries']} {report['window']} entries — {s['pass']} pass, "
        f"{s['fail']} fail, {s['not_measured']} not measured"
    ]
    for e in report["entries"]:
        label = e["status"].replace("_", " ")
        extra = ""
        if e["failed_rules"]:
            extra = "  failed: " + ", ".join(e["failed_rules"])
        elif e["status"] == "not_measured":
            extra = "  " + (
                e.get("reason") or "not measured: " + ", ".join(e["not_measured_rules"])
            )
        lines.append(f"  {e['id']:<28} {label:<12}{extra}")
    if "incidents" in report:
        lines.append("")
        lines.append("incidents")
        for i in report["incidents"]:
            first = f" (first: {i['first_caught_by']})" if i["first_caught_by"] else ""
            lines.append(f"  {i['id']:<16} {i['from']}..{i['to']}  {i['status']}{first}")
        alarms = report["alarms_outside_incidents"]
        lines.append(f"alarms outside incidents: {', '.join(alarms) if alarms else 'none'}")
    if "compare" in report:
        c = report["compare"]
        lines.append("")
        lines.append(f"the two contracts disagree on {c['disagreements']} entries")
        for row in c["entries"]:
            lines.append(f"  {row['id']:<28} new {row['status']}, old {row['old_status']}")
    return "\n".join(lines) + "\n"


def run(a: argparse.Namespace) -> int:
    if a.rules_cmd == "mutate":
        from shape.rules.mutation import MutationError, mutation_test

        if a.min_score is not None and not 0.0 <= a.min_score <= 1.0:
            raise MutationError(f"--min-score is a number from 0 to 1, got {a.min_score}")
        result = mutation_test(
            a.data, a.contract, plan=a.plan, seed=a.seed, rate=a.rate, diff=a.diff
        )
        report = result.to_dict()
        if report["score"]["overall"]["applicable"] == 0:
            raise MutationError("no mutant changed a cell: the data has nothing to corrupt")
        _emit(report, a, mutation_text(report))
        if a.min_score is not None and (result.score or 0.0) < a.min_score:
            print(
                f"shape: mutation score {_pct(result.score)} is below --min-score "
                f"{_pct(a.min_score)}",
                file=sys.stderr,
            )
            return 1
        return 0
    from shape.rules.history import backtest

    if a.fail_on_miss and not a.incidents:
        raise ValueError("--fail-on-miss needs --incidents")
    result_b = backtest(
        a.registry,
        a.name,
        a.contract,
        since=a.since,
        until=a.until,
        window=a.window,
        incidents=a.incidents,
        compare=a.compare,
    )
    report_b = result_b.to_dict()
    _emit(report_b, a, backtest_text(report_b))
    if a.fail_on_miss and result_b.missed:
        print(f"shape: missed incidents: {', '.join(result_b.missed)}", file=sys.stderr)
        return 1
    return 0
