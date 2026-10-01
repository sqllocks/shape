"""The baseline's DDL import, as committed fixtures (runs in the Spindle venv; P4-01b).

    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/ddl_1to1/dump_ddl.py
    source scripts/env.sh && "$SPINDLE_PY" benchmarks/vs_spindle/ddl_1to1/dump_ddl.py --check

Inputs (``fixtures/*.sql``): every DDL fixture in the baseline's own tests (the string constants
that hold ``CREATE TABLE`` statements, and its ``adventureworks_sample.sql``) plus the cases in
``extra/`` that exercise every inference rule. ``--check`` also proves the committed copies of the
baseline's fixtures are still what its tests hold.

For each input, in each mode (``smart``, ``plain``) and with a scale override, the baseline's
``from-ddl`` runs and this writes ``fixtures/expected/<name>.<mode>.json``:

* ``schema``: ``dataclasses.asdict`` of the schema after the import (every field, including the
  business rules and derived counts that the baseline does not write to its file);
* ``annotations``: every inference decision, in order;
* ``file``: the document the baseline's ``from-ddl`` command writes;
* ``explain``: the lines of its ``--explain`` report.

``verify.py`` (Shape venv) compares Shape's import with these. Nothing under ``$SPINDLE_ROOT``
is modified.
"""

from __future__ import annotations

import argparse
import ast
import dataclasses
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import SPINDLE_ROOT  # noqa: E402

FIXTURES = HERE / "fixtures"
EXPECTED = FIXTURES / "expected"
SCALE = "medium:customer=7,orders=21,order=21"
DOMAIN = "shop"


def baseline_inputs() -> dict[str, str]:
    """``name -> DDL text`` of every DDL fixture in the baseline's tests."""
    tests = SPINDLE_ROOT / "tests"
    found: dict[str, str] = {}
    for path in sorted(tests.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and "CREATE TABLE" in node.value.value
            ):
                target = next(t.id for t in node.targets if isinstance(t, ast.Name))
                found[f"{path.stem[5:]}__{target.lower()}"] = node.value.value
    # The command-line test writes its DDL inline.
    found["e2e_cli__inline"] = (
        "CREATE TABLE customer (id INT PRIMARY KEY, name VARCHAR(50));\n"
        "CREATE TABLE orders (id INT PRIMARY KEY, customer_id INT REFERENCES customer(id));\n"
    )
    sample = tests / "fixtures" / "adventureworks_sample.sql"
    found["adventureworks_sample"] = sample.read_text(encoding="utf-8")
    return found


def extra_inputs() -> dict[str, str]:
    return {p.stem: p.read_text(encoding="utf-8") for p in sorted((HERE / "extra").glob("*.sql"))}


def run_one(ddl: str, smart: bool) -> dict[str, Any]:
    sys.path.insert(0, str(SPINDLE_ROOT))
    from click.testing import CliRunner
    from sqllocks_spindle.cli import _apply_scale_overrides, main
    from sqllocks_spindle.schema.ddl_parser import DdlParser
    from sqllocks_spindle.schema.inference import SchemaInferenceEngine

    schema = DdlParser().parse_string(ddl)
    schema.model.domain = DOMAIN
    schema.model.name = f"{DOMAIN}_ddl_import"
    annotations: list[Any] = []
    if smart:
        schema, notes = SchemaInferenceEngine().infer_with_report(schema)
        annotations = [dataclasses.asdict(n) for n in notes]
    _apply_scale_overrides(schema, SCALE)

    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.sql", Path(tmp) / "out.json"
        src.write_text(ddl, encoding="utf-8")
        args = ["from-ddl", str(src), "-o", str(out), "--domain", DOMAIN, "-s", SCALE]
        args += ["--smart" if smart else "--no-smart", "--explain"]
        result = CliRunner().invoke(main, args)
        if result.exit_code != 0:
            raise SystemExit(f"baseline from-ddl failed: {result.output}")
        file_doc = json.loads(out.read_text(encoding="utf-8"))
    lines = result.output.splitlines()
    explain = lines[lines.index("--- Inference Report ---") + 2 :] if smart else []
    return {
        "schema": json.loads(json.dumps(dataclasses.asdict(schema), default=str)),
        "annotations": annotations,
        "file": file_doc,
        "explain": [x for x in explain if x.strip()],
    }


def collect() -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """``(inputs, expected)``; expected is keyed ``<name>.<mode>``."""
    inputs = {**baseline_inputs(), **extra_inputs()}
    expected: dict[str, dict[str, Any]] = {}
    for name, ddl in inputs.items():
        for mode in ("smart", "plain"):
            expected[f"{name}.{mode}"] = run_one(ddl, mode == "smart")
    return inputs, expected


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="compare, do not write")
    args = ap.parse_args(argv)
    inputs, expected = collect()
    if args.check:
        bad = []
        for name, ddl in baseline_inputs().items():
            p = FIXTURES / f"{name}.sql"
            if not p.is_file() or p.read_text(encoding="utf-8") != ddl:
                bad.append(str(p.relative_to(HERE)))
        for key, doc in expected.items():
            p = EXPECTED / f"{key}.json"
            if not p.is_file() or json.loads(p.read_text(encoding="utf-8")) != doc:
                bad.append(str(p.relative_to(HERE)))
        for b in bad:
            print(f"differs: {b}")
        print(f"{len(inputs)} DDL inputs x 2 modes: " + ("DIFFER" if bad else "match the baseline"))
        return 1 if bad else 0
    EXPECTED.mkdir(parents=True, exist_ok=True)
    for name, ddl in baseline_inputs().items():
        (FIXTURES / f"{name}.sql").write_text(ddl, encoding="utf-8", newline="\n")
    for key, doc in expected.items():
        (EXPECTED / f"{key}.json").write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    print(f"wrote {len(inputs)} inputs and {len(expected)} expected outputs under {FIXTURES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
