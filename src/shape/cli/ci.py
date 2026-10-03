"""CI reports of the checking commands: JUnit XML and SARIF 2.1.0 (W1-14).

``shape diff``, ``check``, ``verify``, ``fidelity`` and ``profile validate --safe`` turn what they
evaluated into a list of :class:`Check` (one per evaluated check, passing or not) and
:func:`write_reports` writes ``--junit FILE`` and ``--sarif FILE`` from it, or the paths of the
``ci:`` block of ``shape.yml``. Writing a report never changes a command's exit code.

Messages carry the rule, the place and, for a few statistical rules, a number. They never carry a
value found in the data (an allowed value that was not allowed, a minimum, a leaked string): the
detail of a gate or a leak scan stays in the command's own report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SARIF_SCHEMA = "https://json.schemastore.org/sarif-2.1.0.json"
INFORMATION_URI = "https://github.com/sqllocks/shape"
DOCS = INFORMATION_URI + "/blob/main/docs/"
FINGERPRINT_KEY = "shapeFinding/v1"
LEVELS = {"high": "error", "medium": "warning", "low": "note"}

#: where a rule is explained, by command
HELP = {
    "diff": DOCS + "DRIFT.md#what-is-compared",
    "check": DOCS + "CI.md#contract-rules",
    "verify": DOCS + "VERIFY.md",
    "fidelity": DOCS + "FIDELITY.md#pass-marks",
    "profile validate": DOCS + "PRIVACY_MODEL.md",
}
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
#: contract rules whose expected and observed values are numbers or type names, not data values
_NUMERIC_RULES = frozenset(
    {
        "row_count.min",
        "row_count.max",
        "max_null_rate",
        "min_true_rate",
        "max_true_rate",
        "max_implausible_rate",
    }
)


@dataclass(frozen=True, slots=True)
class Check:
    """One evaluated check. ``status`` is ``pass``, ``fail``, ``error`` or ``skipped`` (a gate in
    observe mode that would fail)."""

    table: str
    column: str
    check: str
    status: str
    level: str = "error"
    message: str = ""
    planned: str | None = None
    rule: str = ""

    @property
    def name(self) -> str:
        return f"{self.column}:{self.check}" if self.column else self.check

    @property
    def rule_id(self) -> str:
        return self.rule or self.check

    @property
    def fqn(self) -> str:
        return f"{self.table}.{self.column}" if self.column else self.table


def clean(text: object) -> str:
    """Text safe to put in XML 1.0."""
    return _XML_BAD.sub("?", str(text))


# ---- JUnit -------------------------------------------------------------------------------------


def junit_xml(command: str, checks: Sequence[Check], seconds: float) -> str:
    """The JUnit document of one command: ``testsuites`` > one ``testsuite`` > ``testcase``s."""
    root = ET.Element("testsuites", name="shape")
    cases = list(checks)
    count = {s: sum(1 for c in cases if c.status == s) for s in ("fail", "error", "skipped")}
    suite = ET.SubElement(
        root,
        "testsuite",
        name=f"shape {command}",
        tests=str(len(cases)),
        failures=str(count["fail"]),
        errors=str(count["error"]),
        skipped=str(count["skipped"]),
        time=f"{max(seconds, 0.0):.3f}",
    )
    for c in cases:
        case = ET.SubElement(
            suite, "testcase", classname=clean(c.table), name=clean(c.name), time="0"
        )
        if c.planned:
            props = ET.SubElement(case, "properties")
            ET.SubElement(props, "property", name="planned", value=clean(c.planned))
        if c.status == "fail":
            ET.SubElement(case, "failure", type=clean(c.check), message=clean(c.message))
        elif c.status == "error":
            ET.SubElement(case, "error", type=clean(c.check), message=clean(c.message))
        elif c.status == "skipped":
            ET.SubElement(case, "skipped", message=clean(c.message))
    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode") + "\n"


# ---- SARIF -------------------------------------------------------------------------------------


def fingerprint(rule_id: str, fqn: str) -> str:
    """The identity of a finding across runs: sha256 of the rule id and ``TABLE.COLUMN``."""
    return hashlib.sha256(f"{rule_id}\n{fqn}".encode()).hexdigest()


def relative_uri(path: str | os.PathLike[str]) -> str:
    """``path`` relative to the working directory, with forward slashes."""
    text = str(path)
    if os.path.isabs(text):
        try:
            text = os.path.relpath(text)
        except ValueError:  # another drive: keep it as given
            pass
    return text.replace("\\", "/")


def sarif_doc(
    command: str, checks: Sequence[Check], input_path: str | os.PathLike[str], version: str
) -> dict[str, Any]:
    """A SARIF 2.1.0 log with one run. Results are the checks that failed, or that would fail in
    observe mode (level ``note``)."""
    uri = relative_uri(input_path)
    rules: dict[str, dict[str, Any]] = {}
    results: list[dict[str, Any]] = []
    for c in checks:
        if c.status not in ("fail", "error", "skipped"):
            continue
        rid = c.rule_id
        rules.setdefault(
            rid,
            {
                "id": rid,
                "shortDescription": {"text": f"{command}: {rid}"},
                "helpUri": HELP.get(command, DOCS + "CI.md"),
            },
        )
        results.append(
            {
                "ruleId": rid,
                "level": "note" if c.status == "skipped" else c.level,
                "message": {"text": clean(c.message or f"{rid} on {c.fqn}")},
                "locations": [
                    {
                        "physicalLocation": {"artifactLocation": {"uri": uri}},
                        "logicalLocations": [{"fullyQualifiedName": clean(c.fqn)}],
                    }
                ],
                "partialFingerprints": {FINGERPRINT_KEY: fingerprint(rid, c.fqn)},
            }
        )
    return {
        "$schema": SARIF_SCHEMA,
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "shape",
                        "version": version,
                        "informationUri": INFORMATION_URI,
                        "rules": list(rules.values()),
                    }
                },
                "results": results,
            }
        ],
    }


# ---- adapters: what each command evaluated -----------------------------------------------------


def _number(x: object) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool)


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def checks_from_diff(
    changes: Iterable[Mapping[str, Any]], tables: Mapping[str, Sequence[str]]
) -> list[Check]:
    """One failing check per change, and a passing ``COLUMN:drift`` for every column of the
    current profile that has none. ``tables`` maps table name to its column names."""
    default = next(iter(tables), "") if len(tables) == 1 else ""
    out: list[Check] = []
    seen: set[tuple[str, str]] = set()
    for ch in changes:
        table = str(ch.get("table") or default)
        column = str(ch.get("column") or "")
        kind = str(ch.get("kind", "change"))
        seen.add((table, column))
        sev = str(ch.get("severity", "medium"))
        bits = [f"{kind} on {table + '.' if table else ''}{column or '(table)'}", f"severity {sev}"]
        if _number(ch.get("score")):
            bits.append(f"score {_fmt(ch['score'])}")
        if _number(ch.get("baseline")) and _number(ch.get("current")) and not ch.get("suppressed"):
            bits.append(f"baseline {_fmt(ch['baseline'])}, current {_fmt(ch['current'])}")
        planned = ch.get("planned")
        out.append(
            Check(
                table,
                column,
                kind,
                "pass" if planned else "fail",
                LEVELS.get(sev, "warning"),
                ", ".join(bits),
                planned=str(planned) if planned else None,
            )
        )
    for table, columns in tables.items():
        for column in columns:
            if (table, column) not in seen:
                out.append(Check(table, column, "drift", "pass"))
    return out


def _contract_rules(contract: Mapping[str, Any]) -> list[tuple[str | None, str]]:
    rules: list[tuple[str | None, str]] = []
    rc = contract.get("row_count")
    for key in ("min", "max"):
        if isinstance(rc, dict) and key in rc:
            rules.append((None, f"row_count.{key}"))
    required = contract.get("required_columns") or []
    rules.extend((str(c), "required_column") for c in required)
    cols = contract.get("columns")
    for name, spec in (cols if isinstance(cols, dict) else {}).items():
        if name not in required:
            rules.append((str(name), "column_exists"))
        rules.extend((str(name), str(r)) for r in (spec if isinstance(spec, dict) else {}))
    return rules


def _violation_message(v: Mapping[str, Any], rule: str, fqn: str) -> str:
    text = f"{rule} failed on {fqn}"
    if rule in _NUMERIC_RULES or rule.endswith(("row_count.min", "row_count.max")):
        e, o = v.get("expected"), v.get("observed")
        if isinstance(e, int | float) and isinstance(o, int | float):
            text += f": expected {_fmt(e)}, observed {_fmt(o)}"
    elif rule == "dtype":
        e, o = v.get("expected"), v.get("observed")
        if isinstance(e, str) and isinstance(o, str):
            text += f": expected {e}, observed {o}"
    return text


def checks_from_contract(
    contract: Mapping[str, Any],
    violations: Iterable[Mapping[str, Any]],
    default_table: str,
    *,
    dataset: bool = False,
) -> list[Check]:
    """Failing checks for the violations of ``shape.check`` and a passing check for every rule
    of the contract that was met. A dataset contract names its tables under ``tables``."""
    failing: list[Check] = []
    keys: set[tuple[str, str | None, str]] = set()
    for v in violations:
        column, rule = v.get("column"), str(v.get("rule", "rule"))
        table = default_table
        if dataset:
            if rule == "table_exists":
                table, rule, column = str(v.get("expected")), "table_exists", None
            elif column is not None and "." in str(column):
                table, _, column = str(column).partition(".")
            elif ":" in rule:
                table, _, rule = rule.partition(":")
        col = None if column is None else str(column)
        keys.add((table, col, rule))
        fqn = f"{table}.{col}" if col else table
        failing.append(
            Check(table, col or "", rule, "fail", "error", _violation_message(v, rule, fqn))
        )
    passing: list[Check] = []
    per_table = contract.get("tables") if dataset else {default_table: contract}
    for table, sub in (per_table if isinstance(per_table, dict) else {}).items():
        if dataset:
            passing.append(Check(str(table), "", "table_exists", "pass"))
            if (str(table), None, "table_exists") in keys:
                passing.pop()
        for col, rule in _contract_rules(sub if isinstance(sub, dict) else {}):
            if (str(table), col, rule) not in keys:
                passing.append(Check(str(table), col or "", rule, "pass"))
    return passing + failing


def checks_from_report(report: Mapping[str, Any]) -> list[Check]:
    """Checks of a ``shape.check`` result on a v2 evidence document (``path``, ``code``)."""
    out = []
    for v in report.get("violations", []):
        path, code = str(v.get("path", "")), str(v.get("code", "rule"))
        level = "warning" if v.get("severity") == "warning" else "error"
        out.append(Check("contract", path, code, "fail", level, f"{code} failed on {path}"))
    if not out:
        out.append(Check("contract", "", "contract", "pass"))
    return out


def checks_from_gates(
    gates: Iterable[Any], modes: Mapping[str, str] | None = None, *, strict: bool = False
) -> list[Check]:
    """One check per gate. A gate that failed in ``observe`` mode is skipped."""
    modes = modes or {}
    out: list[Check] = []
    for g in gates:
        name = g.gate_name
        rule = f"gate.{name}"
        failed = (not g.passed) or (strict and bool(g.warnings))
        n_err, n_warn = len(g.errors), len(g.warnings)
        message = f"gate {name}: {n_err} error(s), {n_warn} warning(s)"
        if not failed:
            out.append(Check("gates", name, "gate", "pass", rule=rule))
        elif modes.get(name, "enforce") == "observe":
            out.append(
                Check(
                    "gates", name, "gate", "skipped", "note", f"observe mode: {message}", rule=rule
                )
            )
        else:
            out.append(
                Check(
                    "gates",
                    name,
                    "gate",
                    "fail",
                    "warning" if g.passed else "error",
                    message,
                    rule=rule,
                )
            )
    return out


def checks_from_fidelity(report: Mapping[str, Any]) -> list[Check]:
    """Checks of a fidelity report: the overall score, each table's score and each column's."""
    marks = report.get("thresholds") or {}
    out: list[Check] = []

    def verdict(score: Any, floor: Any) -> bool:
        return not (_number(score) and _number(floor) and score < floor)

    overall = report.get("overall_score")
    ok = verdict(overall, marks.get("min_overall"))
    msg = "" if ok else f"overall score {overall} < {marks['min_overall']}"
    out.append(
        Check("overall", "", "score", "pass" if ok else "fail", "error", msg, rule="overall")
    )
    for t in report.get("missing_tables", []):
        out.append(Check(str(t), "", "present", "fail", "error", f"table {t} is missing"))
    for t, tf in (report.get("tables") or {}).items():
        t = str(t)
        if not tf.get("present", True):
            continue
        ok = verdict(tf.get("score"), marks.get("min_table"))
        msg = "" if ok else f"table {t} score {_fmt(tf['score'])} < {_fmt(marks['min_table'])}"
        out.append(
            Check(t, "", "score", "pass" if ok else "fail", "error", msg, rule="table_score")
        )
        for c in tf.get("missing_columns", []):
            out.append(Check(t, str(c), "present", "fail", "error", f"column {t}.{c} is missing"))
        for c, cf in (tf.get("columns") or {}).items():
            if not cf.get("present", True):
                continue
            ok = verdict(cf.get("score"), marks.get("min_column"))
            msg = (
                ""
                if ok
                else f"column {t}.{c} score {_fmt(cf['score'])} < {_fmt(marks['min_column'])}"
            )
            out.append(
                Check(
                    t, str(c), "score", "pass" if ok else "fail", "error", msg, rule="column_score"
                )
            )
    if not report.get("passed", True) and not any(c.status == "fail" for c in out):
        out.append(
            Check(
                "overall",
                "",
                "issues",
                "fail",
                "error",
                "; ".join(map(str, report["failures"]))[:500],
            )
        )
    return out


def checks_from_leaks(artifact: str, findings: Iterable[Any]) -> list[Check]:
    """One failing check per leak finding (the rule and the path, never the detail); one passing
    check when the scan was clean."""
    table = Path(artifact).name
    out = [
        Check(table, str(f.path), str(f.rule), "fail", "error", f"{f.rule} at {f.path}")
        for f in findings
    ]
    return out or [Check(table, "", "no_leaks", "pass")]


# ---- flags, defaults and writing ---------------------------------------------------------------


def add_flags(parser: argparse.ArgumentParser) -> None:
    """``--junit FILE`` and ``--sarif FILE``."""
    g = parser.add_argument_group("CI reports (defaults: the `ci:` block of shape.yml)")
    g.add_argument("--junit", metavar="FILE", help="write the checks as JUnit XML")
    g.add_argument("--sarif", metavar="FILE", help="write the findings as SARIF 2.1.0")


def started() -> float:
    return time.perf_counter()


def project_ci(a: argparse.Namespace) -> tuple[Mapping[str, str], Path | None]:
    """The ``ci:`` defaults of the project in effect, and its folder. Empty when there is no
    project or ``--no-project``; a project file that cannot be read is noted and ignored here
    (commands that need the project read it themselves and fail on it)."""
    from shape.project import ProjectError, find_project, load_project

    if getattr(a, "no_project", False):
        return {}, None
    given = getattr(a, "project", None)
    try:
        path = Path(given) if given else find_project()
        if path is None:
            return {}, None
        project = load_project(path)
    except (OSError, ProjectError) as exc:
        print(f"shape: note: ci defaults of shape.yml ignored: {exc}", file=sys.stderr)
        return {}, None
    return project.ci, project.root


def resolve_path(
    flag: str | None, default: str | None, command: str, root: Path | None
) -> Path | None:
    """The flag as given, else the project default with ``{command}`` filled in and relative paths
    placed under the project's folder."""
    if flag:
        return Path(flag)
    if not default:
        return None
    path = Path(default.replace("{command}", command.replace(" ", "-")))
    return path if path.is_absolute() or root is None else root / path


def _write(path: Path, text: str, what: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as exc:
        print(f"shape: warning: could not write {what} {path}: {exc}", file=sys.stderr)


def requested(a: argparse.Namespace) -> bool:
    return bool(getattr(a, "junit", None) or getattr(a, "sarif", None))


def write_reports(
    a: argparse.Namespace,
    command: str,
    checks: Sequence[Check],
    input_path: str | os.PathLike[str],
    t0: float,
    *,
    ci: Mapping[str, str] | None = None,
    root: Path | None = None,
) -> None:
    """Write the JUnit and SARIF reports asked for by flags (or by ``ci`` defaults). A report that
    cannot be written is a warning on stderr; the exit code stays the command's."""
    if ci is None:
        ci, root = project_ci(a)
    junit = resolve_path(getattr(a, "junit", None), ci.get("junit"), command, root)
    sarif = resolve_path(getattr(a, "sarif", None), ci.get("sarif"), command, root)
    if junit is not None:
        _write(junit, junit_xml(command, checks, time.perf_counter() - t0), "JUnit report")
    if sarif is not None:
        from shape import __version__

        doc = sarif_doc(command, checks, input_path, __version__)
        _write(sarif, json.dumps(doc, indent=2) + "\n", "SARIF report")
