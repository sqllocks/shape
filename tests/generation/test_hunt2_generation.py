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


# ---- #652: interpolation keeps the column order, so the output is the same in every process --


_INTERP = """
from shape.generation.timeline import ShapeTimeline, VersionedShape
cols = {f"c{i}": {"kind": "numeric", "mean": i, "variance_population": 1.0} for i in range(8)}
late = {**{f"c{i}": cols[f"c{i}"] for i in (7, 6)}, "only_b": {"kind": "numeric", "mean": 1}}
a = VersionedShape("1", 0.0, {"rows": 5, "columns": cols})
b = VersionedShape("2", 10.0, {"rows": 5, "columns": late})
data, report = ShapeTimeline([a, b]).generate_at(5.0, 4, seed=1)
print(report.fields, [list(map(float, v)) for v in data.values()])
"""


def test_interpolated_columns_keep_their_order_in_every_process() -> None:
    import subprocess
    import sys

    outputs = set()
    for hash_seed in ("1", "2", "3", "4"):
        env = {**__import__("os").environ, "PYTHONHASHSEED": hash_seed}
        run = subprocess.run(
            [sys.executable, "-c", _INTERP], env=env, capture_output=True, text=True, check=True
        )
        outputs.add(run.stdout)
    assert len(outputs) == 1, outputs
    fields = eval(outputs.pop().split(" [[")[0])  # noqa: S307 - our own repr
    # the first shape's columns in its order, then the columns only the second one has
    assert fields == tuple([f"c{i}" for i in range(8)] + ["only_b"])


def test_interpolate_orders_columns_first_shape_then_second() -> None:
    from shape.generation.evolution import ShapePoint, interpolate

    a = ShapePoint(0.0, {"columns": {"z": {"mean": 0}, "a": {"mean": 0}}})
    b = ShapePoint(2.0, {"columns": {"m": {"mean": 4}, "a": {"mean": 4}}})
    out = interpolate(a, b, 1.0)["columns"]
    assert list(out) == ["z", "a", "m"]
    assert out["a"]["mean"] == 2.0 and out["z"] == {"mean": 0} and out["m"] == {"mean": 4}


# ---- #653: a fixed text branch of `conditional` stays text -------------------------------------


def _conditional(true_value: object, false_value: object) -> list[object]:
    from shape.generation.engine import Engine

    cols = {
        "a": {"name": "a", "type": "string", "generator": {"strategy": "choice", "values": ["x", "y"]}},
        "c": {
            "name": "c",
            "type": "string",
            "generator": {
                "strategy": "conditional",
                "condition": "a == x",
                "true_generator": {"fixed": true_value},
                "false_generator": {"fixed": false_value},
            },
        },
    }
    doc = {
        "schema_version": 1,
        "model": {"name": "m", "seed": 3},
        "tables": {"t": {"name": "t", "primary_key": [], "columns": cols}},
        "generation": {"scales": {"s": {"t": 40}}, "scale": "s"},
    }
    table = Engine(GenSchema.from_dict(doc)).generate().tables["t"]
    rows = table.to_pylist()
    assert {r["a"] for r in rows} == {"x", "y"}
    return [table.column("c").type, {(r["a"], r["c"]) for r in rows}]


@pytest.mark.parametrize(
    ("yes", "no"),
    [("02134", "N/A"), ("007", "010"), ("nan", "inf"), ("1e3", "-0"), (" 5", "5 ")],
)
def test_a_fixed_text_branch_stays_text(yes: str, no: str) -> None:
    import pyarrow as pa

    kind, pairs = _conditional(yes, no)
    assert kind == pa.string()
    assert pairs == {("x", yes), ("y", no)}


def test_fixed_numbers_stay_numbers() -> None:
    import pyarrow as pa

    kind, pairs = _conditional(10, 0.0)
    assert kind == pa.float64()
    assert pairs == {("x", 10.0), ("y", 0.0)}
    kind, pairs = _conditional(None, 2)
    assert kind == pa.float64() and pairs == {("x", None), ("y", 2.0)}


def test_a_number_and_a_text_branch_make_a_text_column() -> None:
    import pyarrow as pa

    kind, pairs = _conditional(7, "seven")
    assert kind == pa.string()
    assert pairs == {("x", "7"), ("y", "seven")}
