"""Run consumer contracts against a producer's profile (``shape-consumer-check``, version 1)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from shape.contracts.v1 import ContractError, check

from .contract import ConsumerContract, ConsumerContractError, load

FORMAT = "shape-consumer-check"
VERSION = 1

OwnerOf = Callable[[str, str | None], str | None]


def _no_owner(table: str, column: str | None) -> str | None:
    return None


def find_files(directory: str | Path) -> list[Path]:
    """The ``*.json`` files under ``directory`` (sub-folders too), in name order."""
    root = Path(directory)
    if not root.is_dir():
        raise ConsumerContractError(f"{directory}: is not a folder")
    return sorted(p for p in root.rglob("*.json") if p.is_file())


def load_all(files: Iterable[Path]) -> list[ConsumerContract]:
    """Read every file. When any is invalid, raise one error that lists every problem of every
    file (each prefixed with the file); two contracts of one consumer and source also clash."""
    contracts: list[ConsumerContract] = []
    found: list[str] = []
    for path in files:
        try:
            contracts.append(load(path))
        except ConsumerContractError as exc:
            found.extend(f"{path}: {p}" for p in exc.problems)
    seen: dict[tuple[str, str], str] = {}
    for c in contracts:
        key = (c.source, c.consumer)
        if key in seen:
            found.append(
                f"{c.path}: consumer {c.consumer!r} of source {c.source!r} is also defined in "
                f"{seen[key]}"
            )
        seen.setdefault(key, c.path)
    if found:
        raise ConsumerContractError(
            f"{len(found)} problem(s) in the consumer contracts", tuple(found)
        )
    return contracts


def _split(
    violation: dict[str, Any], tables: dict[str, Any] | None
) -> tuple[str | None, str | None]:
    """``(table, column)`` of a violation of ``check`` (a dataset names columns ``table.column``
    and the rules of a table's own limits ``table:rule``)."""
    if not tables:
        return None, violation.get("column")
    column, rule = violation.get("column"), str(violation.get("rule", ""))
    if column is None:
        if ":" in rule:
            table, _, _ = rule.partition(":")
            return (table, None) if table in tables else (None, None)
        expected = violation.get("expected")
        return (expected, None) if rule == "table_exists" and expected in tables else (None, None)
    for table in sorted(tables, key=len, reverse=True):
        if column.startswith(table + "."):
            return table, column[len(table) + 1 :]
    return None, column


def _rule(violation: dict[str, Any], table: str | None) -> str:
    rule = str(violation["rule"])
    return rule.partition(":")[2] if table and ":" in rule else rule


def _violations(
    profile: Any, contract: ConsumerContract, owner_of: OwnerOf
) -> list[dict[str, Any]]:
    try:
        result = check(profile, contract.requires)
    except ContractError as exc:
        return [
            {
                "table": None,
                "column": None,
                "rule": "contract_not_applicable",
                "expected": "a profile this contract can be checked against",
                "observed": str(exc),
            }
        ]
    out: list[dict[str, Any]] = []
    # A rule a safe capture left without its value (W1-11) is never a silent pass: it is
    # reported, with what it needed, as a violation the producer can fix by a full capture.
    blind = [
        {
            "column": g["column"],
            "rule": g["rule"],
            "expected": _expected(contract.requires, g),
            "observed": f"not evaluable: {g['reason']}",
        }
        for g in result.not_evaluable
    ]
    for v in [*result.violations, *blind]:
        table, column = _split(v, contract.tables)
        record: dict[str, Any] = {
            "table": table,
            "column": column,
            "rule": _rule(v, table),
            "expected": v["expected"],
            "observed": v["observed"],
        }
        owner = owner_of(table or "", column) if column else None
        if owner:
            record["producer_owner"] = owner
        out.append(record)
    return out


def _expected(requires: dict[str, Any], gap: dict[str, Any]) -> Any:
    """The value a not-evaluable rule asked for, from the contract (``None`` when it cannot be
    found, as for a rule the check names differently)."""
    column = str(gap.get("column") or "")
    rule = str(gap.get("rule") or "")
    tables = requires.get("tables")
    if isinstance(tables, dict) and "." in column:
        table, _, column = column.partition(".")
        requires = tables.get(table) or {}
    rules = (requires.get("columns") or {}).get(column) or {}
    return rules.get(rule) if isinstance(rules, dict) else None


def _key(v: dict[str, Any]) -> tuple[Any, Any, Any]:
    return (v["table"], v["column"], v["rule"])


def check_consumers(
    profile: Any,
    contracts: Iterable[ConsumerContract],
    *,
    baseline: Any = None,
    owner_of: OwnerOf = _no_owner,
) -> list[dict[str, Any]]:
    """One result per contract: ``status`` (``pass`` or ``fail``), the violations, and with a
    ``baseline`` profile which of them are new: a rule that passed on the baseline and fails on
    ``profile`` (``new: true``). ``broken_by_change`` is true when a consumer has a new one."""
    results: list[dict[str, Any]] = []
    for c in contracts:
        violations = _violations(profile, c, owner_of)
        entry: dict[str, Any] = {
            "consumer": c.consumer,
            "owner": c.owner,
            "source": c.source,
            "since": c.since,
            "file": c.path,
            "status": "fail" if violations else "pass",
            "violations": violations,
        }
        if baseline is not None:
            before = {_key(v) for v in _violations(baseline, c, owner_of)}
            for v in violations:
                v["new"] = _key(v) not in before
            entry["broken_by_change"] = any(v["new"] for v in violations)
            entry["already_failing"] = bool(violations) and not entry["broken_by_change"]
        results.append(entry)
    return results


def build_report(
    source: str | None,
    profile_info: dict[str, Any],
    results: list[dict[str, Any]],
    *,
    baseline_info: dict[str, Any] | None = None,
    directory: str | None = None,
) -> dict[str, Any]:
    """Build a structured consumer contract result report."""
    failed = sum(1 for r in results if r["status"] == "fail")
    report: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "source": source,
        "directory": directory,
        "profile": profile_info,
        "consumers": results,
        "summary": {"consumers": len(results), "passed": len(results) - failed, "failed": failed},
        "passed": failed == 0,
    }
    if baseline_info is not None:
        report["baseline"] = baseline_info
        report["summary"]["broken_by_change"] = sum(1 for r in results if r.get("broken_by_change"))
    return report


def render_text(report: dict[str, Any]) -> str:
    """Render consumer contract results as a text report."""
    lines: list[str] = []
    s = report["summary"]
    if not report["consumers"]:
        lines.append(f"no consumer contracts for source {report['source']!r}")
    for r in report["consumers"]:
        if r["status"] == "pass":
            lines.append(f"PASS  {r['consumer']}")
            continue
        flag = ""
        if "broken_by_change" in r:
            flag = " (broken by this change)" if r["broken_by_change"] else " (already failing)"
        lines.append(f"FAIL  {r['consumer']}{flag}  owner: {r['owner']}")
        for v in r["violations"]:
            where = ".".join(x for x in (v["table"], v["column"]) if x) or "-"
            new = " [new]" if v.get("new") else ""
            prod = f"  producer owner: {v['producer_owner']}" if v.get("producer_owner") else ""
            lines.append(
                f"      {where}: {v['rule']} expected {v['expected']!r}, observed "
                f"{v['observed']!r}{new}{prod}"
            )
    lines.append(f"{s['passed']} of {s['consumers']} consumers pass")
    return "\n".join(lines) + "\n"
