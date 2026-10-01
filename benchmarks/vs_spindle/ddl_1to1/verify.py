"""Shape's DDL import against the baseline's, input by input (runs in the Shape venv; P4-01b).

    source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/ddl_1to1/verify.py

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
from dump_ddl import DOMAIN, EXPECTED, SCALE  # noqa: E402
from schema_import import to_native  # noqa: E402

from shape.generation.ddl import from_ddl  # noqa: E402


def inputs() -> dict[str, str]:
    paths = sorted(HERE.glob("fixtures/*.sql")) + sorted(HERE.glob("extra/*.sql"))
    return {p.stem: p.read_text(encoding="utf-8") for p in paths}


def diff(a: Any, b: Any, path: str = "") -> list[str]:
    """Where two JSON values differ (at most a few lines per call site)."""
    if type(a) is not type(b):
        return [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        out = []
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: only in {'expected' if k in b else 'shape'}")
            else:
                out += diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path}: {len(a)} items != {len(b)}"]
        return [
            d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in diff(x, y, f"{path}[{i}]")
        ]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def explain_lines(output: str) -> list[str]:
    lines = output.splitlines()
    if "--- Inference Report ---" not in lines:
        return []
    return [x for x in lines[lines.index("--- Inference Report ---") + 2 :] if x.strip()]


def check(name: str, ddl: str, mode: str) -> list[str]:
    smart = mode == "smart"
    want = json.loads((EXPECTED / f"{name}.{mode}.json").read_text(encoding="utf-8"))
    problems: list[str] = []

    schema, notes = from_ddl(ddl, domain=DOMAIN, smart=smart, scale=SCALE)
    problems += diff(schema.to_dict(), to_native(want["schema"]), "schema")
    got = [
        {
            "table": n.table,
            "column": n.column,
            "rule_id": n.rule_id,
            "description": n.description,
            "confidence": n.confidence,
        }
        for n in notes
    ]
    problems += diff(got, want["annotations"], "annotations")
    # Informational: the baseline's output can fail validation (a column-level REFERENCES clause
    # is not read, so a convention key may name a parent column that does not exist). Equality
    # with the baseline is the check; the findings are listed so they are not lost.
    for issue in schema.validate():
        if issue.level == "error":
            print(f"     note ({name} [{mode}]): {issue.location}: {issue.message}")

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
            "--smart" if smart else "--no-smart",
            "--explain",
        ]
        run = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if run.returncode != 0:
            return problems + [f"shape from-ddl exited {run.returncode}: {run.stderr.strip()}"]
        problems += diff(json.loads(out.read_text(encoding="utf-8")), schema.to_dict(), "cli file")
        problems += diff(explain_lines(run.stdout), want["explain"], "explain")

    # What the baseline's command writes is a subset (no rules, no derived counts): equal on it.
    file_doc = schema.to_dict()
    file_doc["business_rules"] = []
    file_doc["generation"]["derived_counts"] = {}
    problems += diff(file_doc, to_native(want["file"]), "baseline file")
    return problems


def main() -> int:
    failed = 0
    cases = 0
    for name, ddl in inputs().items():
        for mode in ("smart", "plain"):
            cases += 1
            problems = check(name, ddl, mode)
            status = "PASS" if not problems else "FAIL"
            print(f"{status} {name} [{mode}]")
            for p in problems[:8]:
                print(f"     {p}")
            failed += bool(problems)
    print(f"{cases - failed}/{cases} equal to the baseline")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
