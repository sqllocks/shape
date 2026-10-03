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


# ---- #654: SpecDocument.save keeps a normal file mode; deep nesting is a SpecError ------------


@pytest.mark.skipif(__import__("os").name != "posix", reason="POSIX file modes")
def test_save_gives_a_new_spec_the_umask_mode_and_keeps_an_existing_mode(tmp_path: Path) -> None:
    import os
    import stat

    old = os.umask(0o022)
    try:
        doc = SpecDocument.from_dict(SPEC)
        new = tmp_path / "new.json"
        doc.save(new)
        assert stat.S_IMODE(new.stat().st_mode) == 0o644
        shared = tmp_path / "shared.json"
        shared.write_text("{}", encoding="utf-8")
        shared.chmod(0o664)
        doc.save(shared)
        assert stat.S_IMODE(shared.stat().st_mode) == 0o664
        assert json.loads(shared.read_text(encoding="utf-8")) == SPEC
        assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []
    finally:
        os.umask(old)


@pytest.mark.parametrize("depth", [600, 5000, 200_000])
def test_deep_nesting_is_a_spec_error(depth: int) -> None:
    from shape.generation.spec_edit import SpecError, validate_text

    text = '{"model":{"name":"m"},"tables":{},"x-deep":' + "[" * depth + "]" * depth + "}"
    problems = validate_text(text)
    assert len(problems) == 1 and "nested" in problems[0].message
    with pytest.raises(SpecError, match="nested"):
        SpecDocument.loads(text)


def test_moderate_nesting_still_loads() -> None:
    text = '{"schema_version":1,"model":{"name":"m"},"tables":{},"x-deep":' + "[" * 200 + "]" * 200 + "}"
    doc = SpecDocument.loads(text)
    assert doc.dumps() == text


# ---- #655: the locale strategy names the real package -------------------------------------------


def test_the_locale_install_hint_names_the_real_package() -> None:
    root = Path(__file__).resolve().parents[2]
    for path in (
        root / "src/shape/builtins/strategies/locale_pack.py",
        root / "docs/LOCALES.md",
        root / "docs/GENERATION_STRATEGIES.md",
    ):
        text = path.read_text(encoding="utf-8")
        assert "sqllocations" not in text, path
    assert "pip install sqllocks-shape-domains" in (
        root / "src/shape/builtins/strategies/locale_pack.py"
    ).read_text(encoding="utf-8")


# ---- #656: validate() reports a NaN null_rate and a negative max_length -------------------------


def _column_issues(**props: object) -> list[str]:
    doc = {
        "schema_version": 1,
        "model": {"name": "m"},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": [],
                "columns": {
                    "c": {
                        "name": "c",
                        "type": "string",
                        "generator": {"strategy": "native", "provider": "first_name"},
                        **props,
                    }
                },
            }
        },
    }
    schema = GenSchema.from_dict(doc)
    return [i.message for i in schema.validate() if i.level == "error"]


def test_a_nan_null_rate_is_an_error() -> None:
    issues = _column_issues(nullable=True, null_rate=float("nan"))
    assert any("null_rate" in m for m in issues), issues
    with pytest.raises(GenSchemaError, match="null_rate"):  # the loader refuses inf already
        _column_issues(nullable=True, null_rate=float("inf"))


@pytest.mark.parametrize("value", [-1, -40])
def test_a_negative_max_length_is_an_error(value: int) -> None:
    issues = _column_issues(max_length=value)
    assert any("max_length" in m for m in issues), issues


@pytest.mark.parametrize(
    "props",
    [{"max_length": 0}, {"max_length": 3}, {"null_rate": 0.0}, {"null_rate": 1.0, "nullable": True}],
)
def test_valid_column_properties_have_no_error(props: dict[str, object]) -> None:
    assert _column_issues(**props) == []


# ---- #682: generate --from a merged profile keeps the value sets the merge lists exactly ------


def _merged_profiles(tmp_path: Path, states: list[str], codes: list[str]) -> tuple[object, object]:
    import shape
    from shape.profile.merge import merge_profiles

    parts = []
    for half in (0, 1):
        path = tmp_path / f"p{half}.csv"
        lines = ["id,state,code,note"]
        for i in range(half * 200, half * 200 + 200):
            lines.append(f"{i},{states[i % len(states)]},{codes[i % len(codes)]},n{i}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        parts.append(shape.profile(str(path), name="p", sketches=True))
    whole = tmp_path / "whole.csv"
    rows = [ln for half in (0, 1) for ln in (tmp_path / f"p{half}.csv").read_text().splitlines()[1:]]
    whole.write_text("id,state,code,note\n" + "\n".join(rows) + "\n", encoding="utf-8")
    return merge_profiles(parts), shape.profile(str(whole), name="p")


def _generated(profile: object, column: str) -> dict[object, int]:
    from collections import Counter

    from shape.generation.engine import Engine
    from shape.generation.fit import fit_schema

    fit = fit_schema(profile)
    table = Engine(fit.schema, seed=4).generate().tables["p"]
    return dict(Counter(table.column(column).to_pylist()))


def test_a_merged_profile_generates_its_exact_value_sets(tmp_path: Path) -> None:
    merged, whole = _merged_profiles(tmp_path, ["WA", "OR", "CA", "WA"], ["02134", "10001"])
    for column, expected in (("state", {"WA", "OR", "CA"}), ("code", {"02134", "10001"})):
        got = _generated(merged, column)
        assert set(got) == expected, (column, got)
    assert set(_generated(whole, "state")) == {"WA", "OR", "CA"}  # as the unmerged profile does
    got = _generated(merged, "state")
    assert got["WA"] > got["OR"] and got["WA"] > got["CA"]  # weights: WA is half the rows


def test_a_merged_column_with_too_many_values_is_not_an_enum(tmp_path: Path) -> None:
    from shape.generation.fit import fit_schema

    merged, _ = _merged_profiles(tmp_path, ["WA", "OR"], ["x"])
    # `note` is unique (n0..n399): more distinct values than the top list holds, never an enum
    gen = fit_schema(merged).schema.tables["p"].columns["note"].generator
    assert gen["strategy"] != "weighted_enum"


# ---- #693: conditional_table keeps output_type "string" labels as text -------------------------


def _joint(output_type: str | None) -> list[object]:
    from shape.generation.engine import Engine

    gen: dict[str, object] = {
        "strategy": "conditional_table",
        "source_column": "state",
        "table": {"WA": {"02134": 0.5, "10001": 0.5}, "OR": {"10001": 1.0}, "CA": {"02134": 1.0}},
        "values": {"02134": 0.5, "10001": 0.5},
    }
    if output_type is not None:
        gen["output_type"] = output_type
    cols = {
        "state": {
            "name": "state",
            "type": "string",
            "generator": {"strategy": "choice", "values": ["WA", "OR", "CA"]},
        },
        "code": {"name": "code", "type": "string", "generator": gen},
    }
    doc = {
        "schema_version": 1,
        "model": {"name": "m", "seed": 2},
        "tables": {"t": {"name": "t", "primary_key": [], "columns": cols}},
        "generation": {"scales": {"s": {"t": 60}}, "scale": "s"},
    }
    table = Engine(GenSchema.from_dict(doc)).generate().tables["t"]
    return [table.column("code").type, set(table.to_pylist()[i]["code"] for i in range(60)), table]


def test_conditional_table_keeps_string_labels_as_text() -> None:
    import pyarrow as pa

    kind, codes, table = _joint("string")
    assert kind == pa.string() and codes == {"02134", "10001"}
    for row in table.to_pylist():
        if row["state"] == "OR":
            assert row["code"] == "10001"
        if row["state"] == "CA":
            assert row["code"] == "02134"


def test_conditional_table_without_output_type_keeps_numbers() -> None:
    import pyarrow as pa

    kind, codes, _ = _joint(None)  # documented: labels that all read as numbers are float64
    assert kind == pa.float64() and codes == {2134.0, 10001.0}
