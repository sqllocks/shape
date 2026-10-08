"""Shape's DDL import against the baseline's, input by input (runs in the Shape venv; P4-01b).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_refengine/ddl_1to1/verify.py

For every input in ``fixtures/`` and ``extra/`` and each mode (``smart``, ``plain``), Shape's
``from_ddl`` result must equal the baseline's (``fixtures/expected/``, written by
``dump_ddl.py``) in every field: the schema as a whole (model, tables, columns, generators,
relationships, business rules, scale presets and derived counts), every inference annotation in
order, and, through the ``shape from-ddl`` command, the written file and the ``--explain``
report. Exit 0 only if every comparison is equal. Standard library plus ``shape``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from differences import ALLOWED, FIXES, Field, Note  # noqa: E402
from dump_ddl import DOMAIN, EXPECTED, SCALE  # noqa: E402
from schema_import import to_native  # noqa: E402

from shape.generation.ddl import from_ddl  # noqa: E402


def inputs() -> dict[str, str]:
    paths = sorted(HERE.glob("fixtures/*.sql")) + sorted(HERE.glob("extra/*.sql"))
    return {p.stem: p.read_text(encoding="utf-8") for p in paths}


def diff_paths(a: Any, b: Any, path: str = "") -> list[tuple[str, str]]:
    """Where two JSON values differ: ``(path, what)`` pairs."""
    if type(a) is not type(b):
        return [(path, f"{a!r} != {b!r}")]
    if isinstance(a, dict):
        out: list[tuple[str, str]] = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append((f"{path}.{k}", f"only in {'expected' if k in b else 'shape'}"))
            else:
                out += diff_paths(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [(path, f"{len(a)} items != {len(b)}")]
        return [
            d
            for i, (x, y) in enumerate(zip(a, b, strict=True))
            for d in diff_paths(x, y, f"{path}[{i}]")
        ]
    return [] if a == b else [(path, f"{a!r} != {b!r}")]


def diff(a: Any, b: Any, path: str = "") -> list[str]:
    """Where two JSON values differ (at most a few lines per call site)."""
    return [f"{p}: {what}" for p, what in diff_paths(a, b, path)]


def keyed(doc: dict[str, Any]) -> dict[str, Any]:
    """The schema document with the relationships and business rules keyed by name, so that a
    difference is reported (and allowed) by what it is, not by list position."""
    out = dict(doc)
    out["relationships"] = {r["name"]: r for r in doc["relationships"]}
    out["business_rules"] = {r["name"]: r for r in doc["business_rules"]}
    return out


def covers(prefix: str, path: str) -> bool:
    """Whether ``path`` is ``prefix`` or below it."""
    return path == prefix or path.startswith((f"{prefix}.", f"{prefix}["))


class Allowed:
    """The intentional differences (``differences.py``) that apply to one input and mode, and
    which of them a comparison used."""

    def __init__(self, name: str, mode: str) -> None:
        entries = [e for e in ALLOWED if e.case == name and mode in e.modes]
        self.paths = [e for e in entries if isinstance(e, Field)]
        self.notes = [e for e in entries if isinstance(e, Note)]
        self.used: set[Field | Note] = set()

    def filter_paths(self, found: list[tuple[str, str]], label: str) -> list[str]:
        """The differences not allowed (``label.path: what``); the allowed ones are recorded."""
        left = []
        for path, what in found:
            rel = path.removeprefix(label).removeprefix(".")
            hit = [e for e in self.paths if covers(f"{e.path}", rel)]
            self.used.update(hit)
            if not hit:
                left.append(f"{path}: {what}")
        return left

    def keys(self) -> set[tuple[str, str, str | None]]:
        return {(n.rule_id, n.table, n.column) for n in self.notes}

    def stale(self) -> list[str]:
        return [
            f"allow-list entry matches no difference: {e}"
            for e in self.paths + self.notes
            if e not in self.used
        ]

    def fixes(self) -> set[str]:
        return {e.fix for e in self.used}


def explain_lines(output: str) -> list[str]:
    lines = output.splitlines()
    if "--- Inference Report ---" not in lines:
        return []
    return [x for x in lines[lines.index("--- Inference Report ---") + 2 :] if x.strip()]


MUST_VALIDATE = ("e2e_cli__inline",)  # inputs whose schema must pass GenSchema.validate()


def annotations_of(notes: list[Any]) -> list[dict[str, Any]]:
    return [
        {
            "table": n.table,
            "column": n.column,
            "rule_id": n.rule_id,
            "description": n.description,
            "confidence": n.confidence,
        }
        for n in notes
    ]


def explain_prefix(rule_id: str, table: str, column: str | None) -> str:
    return f"  [{rule_id}] {table}{'.' + column if column else ''}: "


def compare(name: str, ddl: str, mode: str) -> tuple[list[str], set[str]]:
    """Shape's import against the baseline's, in process: ``(problems, fixes whose intentional
    differences were seen)``. Only the differences listed in ``differences.py`` are accepted."""
    smart = mode == "smart"
    want = json.loads((EXPECTED / f"{name}.{mode}.json").read_text(encoding="utf-8"))
    allowed = Allowed(name, mode)
    schema, notes = from_ddl(ddl, domain=DOMAIN, smart=smart, scale=SCALE)
    doc = schema.to_dict()

    problems = allowed.filter_paths(
        diff_paths(keyed(doc), keyed(to_native(want["schema"])), "schema"), "schema"
    )
    # What the baseline's command writes is a subset (no rules, no derived counts): equal on it.
    file_doc = schema.to_dict()
    file_doc["business_rules"] = []
    file_doc["generation"]["derived_counts"] = {}
    problems += allowed.filter_paths(
        diff_paths(keyed(file_doc), keyed(to_native(want["file"])), "baseline file"),
        "baseline file",
    )

    got, expected = annotations_of(notes), want["annotations"]
    skip = allowed.keys()

    def key(a: dict[str, Any]) -> tuple[str, str, str | None]:
        return (a["rule_id"], a["table"], a["column"])

    problems += diff(
        [a for a in got if key(a) not in skip],
        [a for a in expected if key(a) not in skip],
        "annotations",
    )
    for n in allowed.notes:
        k = (n.rule_id, n.table, n.column)
        if [a for a in got if key(a) == k] != [a for a in expected if key(a) == k]:
            allowed.used.add(n)
    problems += allowed.stale()
    if name in MUST_VALIDATE:
        problems += [
            f"validate: {i.location}: {i.message}" for i in schema.validate() if i.level == "error"
        ]
    return problems, allowed.fixes()


def check(name: str, ddl: str, mode: str) -> tuple[list[str], set[str]]:
    """:func:`compare`, and the same import through ``shape from-ddl`` (the written file and the
    ``--explain`` report)."""
    problems, fixes = compare(name, ddl, mode)
    want = json.loads((EXPECTED / f"{name}.{mode}.json").read_text(encoding="utf-8"))
    schema, _ = from_ddl(ddl, domain=DOMAIN, smart=mode == "smart", scale=SCALE)
    allowed = Allowed(name, mode)
    skip = tuple(explain_prefix(*k) for k in allowed.keys())

    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.sql", Path(tmp) / "out.json"
        src.write_text(ddl, encoding="utf-8")
        cmd = [
            sys.executable,
            "-m",
            "shape.cli.main",
            "from-ddl",
            str(src),
            "-o",
            str(out),
            "--domain",
            DOMAIN,
            "-s",
            SCALE,
            "--smart" if mode == "smart" else "--no-smart",
            "--explain",
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", check=False)
        if run.returncode != 0:
            return problems + [
                f"shape from-ddl exited {run.returncode}: {run.stderr.strip()}"
            ], fixes
        problems += diff(json.loads(out.read_text(encoding="utf-8")), schema.to_dict(), "cli file")
        problems += diff(
            [x for x in explain_lines(run.stdout) if not x.startswith(skip)],
            [x for x in want["explain"] if not x.startswith(skip)],
            "explain",
        )
    return problems, fixes


def main() -> int:
    failed = 0
    cases = 0
    seen: dict[str, int] = {}
    for name, ddl in inputs().items():
        for mode in ("smart", "plain"):
            cases += 1
            problems, fixes = check(name, ddl, mode)
            status = "PASS" if not problems else "FAIL"
            tag = f"  (intentional: {', '.join(sorted(fixes))})" if fixes else ""
            print(f"{status} {name} [{mode}]{tag}")
            for f in fixes:
                seen[f] = seen.get(f, 0) + 1
            for p in problems[:8]:
                print(f"     {p}")
            failed += bool(problems)
    print()
    for fix, text in FIXES.items():
        print(f"{fix}: seen in {seen.get(fix, 0)} case(s). {text}")
    unseen = [f for f in FIXES if f not in seen]
    if unseen:
        print(f"no case shows the intentional difference of {', '.join(unseen)}")
    print(f"{cases - failed}/{cases} equal to the baseline, apart from the listed differences")
    return 1 if failed or unseen else 0


if __name__ == "__main__":
    raise SystemExit(main())
