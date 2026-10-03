"""Regression tests for the second bug hunt of the generation area (lane HUNT2-generation)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.generation.schema import GenSchema, GenSchemaError
from shape.generation.spec_edit import SpecDocument
from shape.migrate import migrate_file

SPEC = {
    "schema_version": 1,
    "model": {"name": "m", "seed": 5},
    "tables": {
        "t": {
            "name": "t",
            "primary_key": ["id"],
            "columns": {
                "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}}
            },
        }
    },
}


# ---- #651: a migrated generation schema loads ----------------------------------------------


def test_a_migrated_generation_schema_loads_and_validates(tmp_path: Path) -> None:
    src = tmp_path / "s.json"
    src.write_text(json.dumps(SPEC), encoding="utf-8")
    dst = tmp_path / "m.json"
    result = migrate_file(src, dst)
    assert result.written
    migrated = json.loads(dst.read_text(encoding="utf-8"))
    assert migrated["migrated_from"] == 1 and "source_content_id" in migrated
    schema = GenSchema.from_dict(migrated)
    assert list(schema.tables) == ["t"]
    assert [str(p) for p in SpecDocument.load(dst).validate() if p.level == "error"] == []


@pytest.mark.parametrize(
    ("key", "value"),
    [("migrated_from", 0), ("migrated_from", "1"), ("migrated_from", True), ("source_content_id", 5)],
)
def test_migration_keys_of_the_wrong_type_are_refused(key: str, value: object) -> None:
    with pytest.raises(GenSchemaError, match=key):
        GenSchema.from_dict({**SPEC, key: value})
