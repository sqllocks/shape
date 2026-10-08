"""P4-01a: the baseline's own schemas import into the Shape generation schema and export back.

``benchmarks/vs_refengine/fixtures/schemas`` holds the 14 domains' schemas in both modes
(``plan_fixtures.py --check`` proves they still equal the baseline's output). Nothing here needs
the baseline's venv.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_refengine"
FIXTURES = BENCH / "fixtures"
sys.path.insert(0, str(BENCH))

_spec = importlib.util.spec_from_file_location(
    "vs_refengine_schema_import", BENCH / "schema_import.py"
)
assert _spec and _spec.loader
schema_import = importlib.util.module_from_spec(_spec)
sys.modules["vs_refengine_schema_import"] = schema_import
_spec.loader.exec_module(schema_import)

from shape.generation.schema import GenSchema, schema_problems  # noqa: E402

DUMPS = sorted((FIXTURES / "schemas").glob("*.json"))
DOMAINS = {
    "capital_markets", "education", "financial", "healthcare", "hr", "insurance", "iot",
    "manufacturing", "marketing", "pulse", "real_estate", "retail", "supply_chain", "telecom",
}  # fmt: skip


def _load(path: Path) -> dict:
    return json.loads(path.read_text("utf-8"))


def test_every_domain_has_both_modes():
    assert {p.stem for p in DUMPS} == {f"{d}_{m}" for d in DOMAINS for m in ("3nf", "star")}


@pytest.mark.parametrize("path", DUMPS, ids=lambda p: p.stem)
def test_dump_imports_validates_and_re_exports_equal(path):
    raw = _load(path)
    schema = schema_import.import_dump(raw)
    assert isinstance(schema, GenSchema)
    assert [i for i in schema.validate() if i.level == "error"] == []
    assert schema_import.export_dump(schema) == raw
    native = schema.to_dict()
    assert schema_problems(native) == []
    assert GenSchema.from_dict(json.loads(json.dumps(native))).to_dict() == native
    assert schema.model.schema_mode in path.stem
    assert not [i for i in schema.validate() if "Unknown strategy" in i.message]


def test_the_importer_cli_checks_every_fixture(capsys):
    assert schema_import.main([]) == 0
    assert "28/28 dumps import and re-export equal" in capsys.readouterr().out


def test_importer_expands_the_short_forms_of_a_hand_written_file():
    seq = {"strategy": "sequence"}
    raw = {
        "model": {"name": "x"},
        "tables": {
            "a": {"columns": {"id": {"generator": seq}}, "primary_key": ["id"]},
            "b": {"columns": {"a_id": {"type": "integer", "generator": seq}}},
        },
        "relationships": [
            {
                "name": "r",
                "parent_table": "a",
                "child_table": "b",
                "parent_key": "id",
                "child_key": ["a_id"],
            }
        ],
    }
    s = schema_import.import_dump(raw)
    r = s.relationships[0]
    assert (r.parent, r.child, r.parent_columns, r.child_columns) == ("a", "b", ["id"], ["a_id"])
    assert s.tables["a"].columns["id"].type == "string" and s.model.seed == 42
    assert s.generation.scale == "small" and s.business_rules == []
