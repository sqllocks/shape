"""One report for a dbt run and a Shape gate: ``run_results.json`` and ``manifest.json`` from a dbt
run, with a contract check and a drift comparison from Shape.

A failed dbt test and a Shape finding about the same column appear together, so the person who
reads the report does not open two tools. Everything is read from files (the dbt run output the
Fabric dbt job writes to OneLake, a ``shape check --json`` and a ``shape diff --json`` result, or
profiles and a contract to compute them), so it works for any dbt Core run.

    shape dbt-report --run-results target/run_results.json --manifest target/manifest.json \\
        --check-result check.json --diff-result diff.json -o report.json --md report.md
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

FORMAT = "shape-dbt-report"
VERSION = 1
FAILED = ("fail", "error")  # a test fails, a model or seed errors; ``warn`` does not fail the run
_MAX_BYTES = 512 * 1024 * 1024


class ReportError(ValueError):
    """An input of the report is not what it should be."""


def load_json(path: str | Path, what: str) -> Any:
    p = Path(path)
    try:
        if p.stat().st_size > _MAX_BYTES:
            raise ReportError(f"{what}: {p} is larger than {_MAX_BYTES // (1024 * 1024)} MB")
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportError(f"{what}: {p} is not readable JSON: {exc}") from exc


def _test_node(node: Mapping[str, Any]) -> dict[str, Any]:
    from .project import ref_target

    meta = node.get("test_metadata") or {}
    namespace = meta.get("namespace")
    name = meta.get("name") or node.get("name")
    attached = str(node.get("attached_node") or "")
    model = attached.rsplit(".", 1)[-1] if attached else None
    if model is None:  # a source test has no attached_node: the `model` argument names the table
        target = ref_target((meta.get("kwargs") or {}).get("model"))
        model = target[0] if target else None
    if model is None:
        deps = [d for d in (node.get("depends_on") or {}).get("nodes", []) if "." in d]
        model = deps[-1].rsplit(".", 1)[-1] if deps else None
    return {
        "test": f"{namespace}.{name}" if namespace else name,
        "model": model,
        "column": node.get("column_name") or (meta.get("kwargs") or {}).get("column_name"),
        "tags": list(node.get("tags") or []),
    }


def dbt_findings(
    run_results: Mapping[str, Any], manifest: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Every result of the run as a finding: its status, message, failure count and, from the
    manifest, the model, column and test it belongs to."""
    if "results" not in run_results:
        raise ReportError("run_results: not a dbt run_results.json (there is no 'results' list)")
    nodes: Mapping[str, Any] = {**((manifest or {}).get("nodes") or {})}
    nodes = {**nodes, **((manifest or {}).get("sources") or {})}
    out = []
    for r in run_results["results"]:
        uid = str(r.get("unique_id", ""))
        node = nodes.get(uid) or {}
        kind = node.get("resource_type") or uid.split(".", 1)[0]
        item: dict[str, Any] = {
            "unique_id": uid,
            "resource_type": kind,
            "name": node.get("name") or uid.rsplit(".", 1)[-1],
            "status": str(r.get("status", "")),
            "message": r.get("message"),
            "failures": r.get("failures"),
            "execution_time": r.get("execution_time"),
        }
        if kind == "test":
            item.update(_test_node(node))
        else:
            item.update({"model": node.get("name"), "column": None, "test": None, "tags": []})
        out.append(item)
    return out


def _by_column(
    failed: Iterable[Mapping[str, Any]],
    violations: Iterable[Mapping[str, Any]],
    changes: Iterable[Mapping[str, Any]],
    default_table: str | None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    joined: dict[str, dict[str, list[dict[str, Any]]]] = {}

    def slot(key: str) -> dict[str, list[dict[str, Any]]]:
        return joined.setdefault(key, {"dbt": [], "contract": [], "drift": []})

    def qualify(column: Any) -> str | None:
        if not column:
            return None
        text = str(column)
        return text if "." in text or not default_table else f"{default_table}.{text}"

    for f in failed:
        if f.get("column") and f.get("model"):
            slot(f"{f['model']}.{f['column']}")["dbt"].append(dict(f))
    for v in violations:
        key = qualify(v.get("column"))
        if key:
            slot(key)["contract"].append(dict(v))
    for c in changes:
        key = qualify(c.get("column"))
        if key:
            slot(key)["drift"].append(dict(c))
    return {k: v for k, v in joined.items() if sum(1 for part in v.values() if part) > 1}


def build_report(
    run_results: Mapping[str, Any],
    manifest: Mapping[str, Any] | None = None,
    *,
    check: Mapping[str, Any] | None = None,
    drift: Mapping[str, Any] | None = None,
    table: str | None = None,
    fail_on_drift: bool = False,
) -> dict[str, Any]:
    """The combined report as a JSON-ready dict.

    ``check`` is a ``shape check`` result (``{"passed", "violations"}``) and ``drift`` a
    ``shape diff`` result (``{"drifted", "changes"}``); either may be left out. ``table`` is the
    table a single-table check or diff is about (its columns are then ``table.column``, so they
    meet the dbt findings of the same model). ``ok`` is false when a dbt test or model failed, a
    contract rule was violated, or, with ``fail_on_drift``, the data drifted."""
    findings = dbt_findings(run_results, manifest)
    counts: dict[str, int] = {}
    for f in findings:
        counts[f["status"]] = counts.get(f["status"], 0) + 1
    failed = [f for f in findings if f["status"] in FAILED]
    warned = [f for f in findings if f["status"] == "warn"]
    violations = list((check or {}).get("violations") or [])
    changes = list((drift or {}).get("changes") or [])
    ok = (
        not failed
        and not violations
        and not (changes and fail_on_drift)
        and (check is None or bool(check.get("passed", not violations)))
    )
    meta = run_results.get("metadata") or {}
    return {
        "format": FORMAT,
        "version": VERSION,
        "ok": ok,
        "dbt": {
            "dbt_version": meta.get("dbt_version"),
            "generated_at": meta.get("generated_at"),
            "counts": dict(sorted(counts.items())),
            "failed": failed,
            "warned": warned,
        },
        "shape": {
            "contract": None
            if check is None
            else {"passed": not violations, "violations": violations},
            "drift": None
            if drift is None
            else {"drifted": bool(changes), "changes": changes, "fail_on_drift": fail_on_drift},
        },
        "by_column": _by_column(failed, violations, changes, table),
        "summary": {
            "dbt_failed": len(failed),
            "dbt_warned": len(warned),
            "dbt_total": len(findings),
            "contract_violations": len(violations),
            "drift_changes": len(changes),
        },
    }


def _cell(value: Any, limit: int = 80) -> str:
    text = json.dumps(value, default=str) if not isinstance(value, str) else value
    text = text.replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_markdown(report: Mapping[str, Any]) -> str:
    s = report["summary"]
    lines = [
        f"# dbt and Shape: {'PASS' if report['ok'] else 'FAIL'}",
        "",
        f"- dbt: {s['dbt_failed']} failed, {s['dbt_warned']} warned, of {s['dbt_total']} results"
        + (f" (dbt {report['dbt']['dbt_version']})" if report["dbt"].get("dbt_version") else ""),
    ]
    contract = report["shape"]["contract"]
    drift = report["shape"]["drift"]
    lines.append(
        "- Shape contract: not run"
        if contract is None
        else f"- Shape contract: {'passed' if contract['passed'] else 'FAILED'}, "
        f"{s['contract_violations']} violation(s)"
    )
    lines.append(
        "- Shape drift: not run"
        if drift is None
        else f"- Shape drift: {'drifted' if drift['drifted'] else 'none'}, "
        f"{s['drift_changes']} change(s)"
    )
    if report["dbt"]["failed"]:
        lines += [
            "",
            "## dbt failures",
            "",
            "| status | model | column | test | message |",
            "|---|---|---|---|---|",
        ]
        for f in report["dbt"]["failed"]:
            lines.append(
                f"| {f['status']} | {_cell(f.get('model'))} | {_cell(f.get('column'))} | "
                f"{_cell(f.get('test') or f['name'])} | {_cell(f.get('message'))} |"
            )
    if contract and contract["violations"]:
        lines += [
            "",
            "## Contract violations",
            "",
            "| column | rule | expected | observed |",
            "|---|---|---|---|",
        ]
        for v in contract["violations"]:
            lines.append(
                f"| {_cell(v.get('column'))} | {_cell(v.get('rule'))} | "
                f"{_cell(v.get('expected'))} | {_cell(v.get('observed'))} |"
            )
    if drift and drift["changes"]:
        lines += [
            "",
            "## Drift",
            "",
            "| column | kind | severity | baseline | current |",
            "|---|---|---|---|---|",
        ]
        for c in drift["changes"]:
            lines.append(
                f"| {_cell(c.get('column'))} | {_cell(c.get('kind'))} | "
                f"{_cell(c.get('severity'))} | {_cell(c.get('baseline'))} | "
                f"{_cell(c.get('current'))} |"
            )
    if report["by_column"]:
        lines += ["", "## Columns flagged by more than one check", ""]
        for column, parts in report["by_column"].items():
            seen = ", ".join(f"{k} ({len(v)})" for k, v in parts.items() if v)
            lines.append(f"- `{column}`: {seen}")
    return "\n".join(lines) + "\n"
