"""Import the baseline's schema dumps into the Shape generation schema, and export them back.

Internal harness code (P4-01a): the product reads only Shape's own generation schema
(``shape.generation.schema``). This module reads the JSON that ``dump_schema.py`` writes (the
baseline's ``dataclasses.asdict`` of one domain schema) and also a hand-written schema file in
the baseline's layout, whose short forms it expands the way the baseline's parser does.

    python benchmarks/vs_refengine/schema_import.py [DUMP.json ...]

With no arguments it checks every dump in ``fixtures/schemas/``: each must import into a
``GenSchema`` that validates, and export back to a document equal (as dicts) to the dump.

Runs in the Shape venv. Standard library plus ``shape``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "schemas"

from shape.generation.schema import SCHEMA_VERSION, GenSchema  # noqa: E402


def _cols(value: Any) -> list[str]:
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _relationship(r: dict[str, Any]) -> dict[str, Any]:
    # The baseline's parser accepts scalar `parent_key` / `child_key` for the column lists and
    # `parent_table` / `child_table` for the table names.
    return {
        "name": r.get("name", ""),
        "parent": r.get("parent") or r.get("parent_table", ""),
        "child": r.get("child") or r.get("child_table", ""),
        "parent_columns": r.get("parent_columns") or _cols(r.get("parent_key")),
        "child_columns": r.get("child_columns") or _cols(r.get("child_key")),
        "type": r.get("type", "one_to_many"),
        "cardinality": r.get("cardinality", {}),
        "optional": r.get("optional", False),
    }


def to_native(raw: dict[str, Any]) -> dict[str, Any]:
    """A baseline-layout document (a dump, or a hand-written file) as a Shape document."""
    model = raw.get("model", {})
    gen = raw.get("generation", {})
    tables: dict[str, Any] = {}
    for tname, t in raw.get("tables", {}).items():
        columns = {
            cname: {
                "name": cname,
                "type": c.get("type", "string"),
                "generator": c.get("generator", {}),
                "nullable": c.get("nullable", False),
                "null_rate": c.get("null_rate", 0.0),
                "max_length": c.get("max_length"),
                "precision": c.get("precision"),
                "scale": c.get("scale"),
            }
            for cname, c in t.get("columns", {}).items()
        }
        tables[tname] = {
            "name": tname,
            "description": t.get("description", ""),
            "cdm_mapping": t.get("cdm_mapping"),
            "primary_key": t.get("primary_key", []),
            "columns": columns,
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "model": {
            "name": model.get("name", "unnamed"),
            "description": model.get("description", ""),
            "domain": model.get("domain", ""),
            "schema_mode": model.get("schema_mode", "3nf"),
            "locale": model.get("locale", "en_US"),
            "seed": model.get("seed", 42),
            "date_range": model.get("date_range", {}),
        },
        "tables": tables,
        "relationships": [_relationship(r) for r in raw.get("relationships", [])],
        "business_rules": [
            {
                "name": b.get("name", ""),
                "type": b.get("type", "constraint"),
                "rule": b.get("rule", ""),
                "table": b.get("table"),
                "via": b.get("via"),
                "when": b.get("when"),
            }
            for b in raw.get("business_rules", [])
        ],
        "generation": {
            "scale": gen.get("scale", "small"),
            "scales": gen.get("scales", {}),
            "derived_counts": gen.get("derived_counts", {}),
            "output": gen.get("output", {}),
        },
        "correlated_columns": raw.get("correlated_columns", {}),
    }


def import_dump(raw: dict[str, Any]) -> GenSchema:
    """The baseline's schema document as a :class:`GenSchema`."""
    return GenSchema.from_dict(to_native(raw))


def export_dump(schema: GenSchema) -> dict[str, Any]:
    """A :class:`GenSchema` in the baseline's layout: ``import_dump`` followed by this gives
    back a dump unchanged."""
    doc = schema.to_dict()
    doc.pop("schema_version")
    return doc


def check(path: Path) -> list[str]:
    raw = json.loads(path.read_text("utf-8"))
    schema = import_dump(raw)
    problems = [f"{i.location}: {i.message}" for i in schema.validate() if i.level == "error"]
    if export_dump(schema) != raw:
        problems.append("re-export differs from the dump")
    if GenSchema.from_dict(schema.to_dict()).to_dict() != schema.to_dict():
        problems.append("native round trip differs")
    return problems


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv] or sorted(FIXTURES.glob("*.json"))
    if not paths:
        print(f"no dumps found in {FIXTURES}", file=sys.stderr)
        return 2
    bad = 0
    for p in paths:
        problems = check(p)
        print(f"{'FAIL' if problems else 'ok  '} {p.name}" + "".join(f"\n  {x}" for x in problems))
        bad += bool(problems)
    print(f"{len(paths) - bad}/{len(paths)} dumps import and re-export equal")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
