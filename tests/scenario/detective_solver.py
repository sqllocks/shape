"""An automated detective: solves a case with Shape's own commands (W6-03 item 3).

``solve(case_dir, run)`` profiles the case's data with ``shape profile``, compares it with the
baseline using ``shape diff``, writes a contract from the baseline and checks the data with
``shape check``; then it turns the checks that fired into findings through the failure mode
catalog: a failure mode is reported for a column when every check that mode names (the ones
``profile``, ``diff`` and ``check`` can show) fired for it, and a mode whose checks another
reported mode already covers is not reported again. A dependency that broke is explained by the
column whose own problem broke it; a table that lost one column and gained one has renamed it.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import shape
from shape.scenario.library import catalog
from shape.scenario.library.detect import baseline_contract

Run = Callable[..., tuple[int, str, str]]
SCHEMA_MODES = {"column-added", "column-removed", "column-dropped", "column-renamed"}
IGNORED = {"drift:implausible_rate_change", "drift:dependency_broken"}


def _split(column: str | None) -> tuple[str, str | None]:
    """``("table", "column")`` of a ``shape diff`` or ``shape check`` column name."""
    if not column:
        return "", None
    table, _, rest = column.partition(".")
    return table, (rest or None)


def fired_checks(case: Path, run: Run) -> dict[tuple[str, str | None], set[str]]:
    today = case / "today.shape"
    code, _, _ = run(
        "profile", case / "data", "--dataset", "--joint", "-o", today, "--capture", "full"
    )
    assert code == 0
    code, out, _ = run("diff", case / "baseline.shape", today, "--json", "-")
    assert code == 0
    fired: dict[tuple[str, str | None], set[str]] = {}
    for change in json.loads(out)["changes"]:
        table, column = _split(change["column"])
        if change["kind"] == "dependency_broken":
            continue
        fired.setdefault((table, column), set()).add(f"drift:{change['kind']}")
    base = shape.load(str(case / "baseline.shape"))
    contract = {
        "tables": {name: baseline_contract(t) for name, t in base.to_dict()["tables"].items()}
    }
    path = case / "contract.json"
    path.write_text(json.dumps(contract))
    code, out, _ = run("check", today, path, "--json", "-")
    assert code in (0, 1)
    for v in json.loads(out)["violations"]:
        rule = v["rule"]
        table, column = _split(v["column"])
        if v["column"] is None:  # a table rule: "order:row_count.min"
            table, _, rule = rule.partition(":")
        fired.setdefault((table, column), set()).add(f"rule:{rule}")
    return fired


def dependencies(case: Path, run: Run) -> dict[str, list[tuple[str, str]]]:
    code, out, _ = run("diff", case / "baseline.shape", case / "today.shape", "--json", "-")
    found: dict[str, list[tuple[str, str]]] = {}
    for change in json.loads(out)["changes"]:
        if change["kind"] == "dependency_broken":
            table, pair = _split(change["column"])
            left, _, right = (pair or "").partition(" -> ")
            found.setdefault(table, []).append((left, right))
    return found


def solve(case: Path, run: Run) -> list[dict[str, Any]]:
    fired = fired_checks(case, run)
    modes = {m["id"]: m for m in catalog.load_catalog()}
    findings: list[dict[str, Any]] = []
    # schema changes: a table that lost one column and gained one renamed it
    for table in sorted({t for t, _ in fired}):
        removed = sorted(
            c for (t, c), f in fired.items() if t == table and "drift:column_removed" in f and c
        )
        added = sorted(
            c for (t, c), f in fired.items() if t == table and "drift:column_added" in f and c
        )
        if len(removed) == 1 and len(added) == 1:
            findings.append({"table": table, "column": removed[0], "mode": "column-renamed"})
            continue
        findings += [{"table": table, "column": c, "mode": "column-dropped"} for c in removed]
        findings += [{"table": table, "column": c, "mode": "column-added"} for c in added]
    # everything else: the modes whose visible checks all fired for the column
    explained: set[tuple[str, str | None]] = set()
    for (table, column), checks in sorted(fired.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        candidates = []
        for mode_id, mode in modes.items():
            if mode_id in SCHEMA_MODES:
                continue
            need = {c for c in mode["detected_by"] if not c.startswith("gate:")}
            if need and need <= checks:
                candidates.append((mode_id, need))
        covered: set[str] = set()
        for mode_id, need in sorted(candidates, key=lambda c: (-len(c[1]), c[0])):
            if need <= covered:
                continue
            covered |= need
            findings.append({"table": table, "column": column, "mode": mode_id})
            explained.add((table, column))
    # a broken dependency is a changed relationship unless a column's own problem broke it
    for table, deps in dependencies(case, run).items():
        open_deps = [
            (a, b) for a, b in deps if (table, a) not in explained and (table, b) not in explained
        ]
        if not open_deps:
            continue
        count = Counter(c for dep in open_deps for c in dep)
        top = max(count.values())
        column = open_deps[0][1] if top == 1 else sorted(c for c, n in count.items() if n == top)[0]
        findings.append({"table": table, "column": column, "mode": "concept-drift"})
    return findings
