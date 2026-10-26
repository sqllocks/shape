"""W9-03 dry-run DDL uses offline plugin hooks and generates no values."""

import json

import pytest

from shape.cli.main import main


@pytest.mark.parametrize(
    "target,needle",
    [
        ("postgresql://shape@localhost/shape", "NUMERIC(12,4)"),
        ("mysql://shape@localhost/shape", "DECIMAL(12,4)"),
    ],
)
@pytest.mark.parametrize("json_mode", [False, True])
def test_wanted5_generate_dry_run_resolves_ddl(
    tmp_path, capsys, monkeypatch, target, needle, json_mode
):
    from shape.generation.engine import Engine

    monkeypatch.setattr(Engine, "generate", lambda *a, **k: pytest.fail("generated values"))
    doc = {
        "schema_version": 1,
        "model": {"name": "type_test"},
        "tables": {
            "items": {
                "name": "items",
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "amount": {
                        "name": "amount",
                        "type": "decimal",
                        "precision": 12,
                        "scale": 4,
                        "generator": {
                            "strategy": "uniform",
                            "min": 0,
                            "max": 1,
                            "output_type": "decimal",
                        },
                    },
                    "text": {
                        "name": "text",
                        "type": "string",
                        "max_length": 40,
                        "generator": {"strategy": "choice", "values": ["a"]},
                    },
                },
            }
        },
    }
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(doc))
    assert (
        main(
            ["generate", str(path), "--to", target, "--dry-run", *(["--json"] if json_mode else [])]
        )
        == 0
    )
    out = capsys.readouterr().out
    if json_mode:
        out = json.loads(out)["ddl"][0]["sql"]
    assert "CREATE TABLE" in out and needle in out and "(40)" in out
    assert len(list(tmp_path.iterdir())) == 1


def test_wanted1_original_retail_without_decimal_dimensions_is_refused(tmp_path, capsys):
    from shape.generation.domains import load_domain

    original = load_domain("retail").schema
    assert original.tables["address"].columns["lat"].precision is None
    assert original.tables["address"].columns["lat"].scale is None
    code = main(["generate", "retail", "--to", "postgresql://shape@localhost/shape", "--dry-run"])
    assert code == 2
    assert "address.lat" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "declared,unit,output_type",
    [("timestamp", "s", None), ("date", "ms", None), ("date", "us", "date")],
)
def test_wanted5_temporal_preview_matches_actual_write(tmp_path, declared, unit, output_type):
    import argparse

    from shape_databases import PostgresSink

    from shape.cli.generation import _target_ddl
    from shape.generation.engine import Engine
    from shape.generation.output import sql_options
    from shape.generation.schema import GenSchema

    generator = {"strategy": "temporal", "unit": unit, "granularity": "day"}
    if output_type:
        generator["output_type"] = output_type
    schema = GenSchema.from_dict(
        {
            "schema_version": 1,
            "model": {"name": "t"},
            "tables": {
                "items": {
                    "name": "items",
                    "columns": {"at": {"name": "at", "type": declared, "generator": generator}},
                }
            },
        }
    )
    args = argparse.Namespace(
        to=["postgresql://shape@localhost/db"],
        format="summary",
        write_mode=None,
        roll_rows=None,
        roll_seconds=None,
        commit_rows=None,
        manifest=False,
    )
    preview = _target_ddl(args, schema)[0]["sql"]
    result = Engine(schema, row_counts={"items": 1}).generate()
    actual = PostgresSink().ddl(
        args.to[0], "items", result.tables["items"].schema, **sql_options(schema, "items")
    )
    assert preview == actual


def test_wanted5_preview_never_resolves_vault_credentials(tmp_path, monkeypatch):
    import argparse

    from shape.cli.generation import _target_ddl
    from shape.generation.schema import GenSchema
    from shape.security import credrefs as credentials

    monkeypatch.setattr(
        credentials, "resolve_reference", lambda *a, **k: pytest.fail("resolved secret")
    )
    schema = GenSchema.from_dict(
        {
            "schema_version": 1,
            "model": {"name": "t"},
            "tables": {
                "items": {
                    "name": "items",
                    "columns": {
                        "id": {
                            "name": "id",
                            "type": "integer",
                            "generator": {"strategy": "sequence"},
                        }
                    },
                }
            },
        }
    )
    args = argparse.Namespace(
        to=["postgresql://shape@localhost/db"],
        format="summary",
        write_mode=None,
        roll_rows=None,
        roll_seconds=None,
        commit_rows=None,
        manifest=False,
        sink_config=["postgres.password=kv://vault-one/password"],
    )
    assert "CREATE TABLE" in _target_ddl(args, schema)[0]["sql"]


@pytest.mark.parametrize("feature", ["checks", "defaults", "nested"])
def test_offline_ddl_retains_constraint_and_nested_options(monkeypatch, feature):
    import pyarrow as pa
    from shape_databases import PostgresSink

    sink = PostgresSink(connect=lambda **kw: pytest.fail("connected"))
    schema = pa.schema([("id", pa.int32())])
    options = {}
    if feature == "checks":
        options["checks"] = [
            {
                "name": "positive",
                "expression": {"op": ">", "left": {"column": "id"}, "right": {"literal": 0}},
            }
        ]
        expected = 'CONSTRAINT "positive" CHECK'
    elif feature == "defaults":
        options["defaults"] = {"id": 7}
        expected = "DEFAULT 7"
    else:
        schema = pa.schema([("id", pa.list_(pa.int32()))])
        options["nested"] = "json"
        expected = '"id" TEXT'
    ddl = sink.ddl("postgresql://shape@localhost/db", "items", schema, **options)
    assert expected in ddl


@pytest.mark.parametrize(
    "mode,expected", [("native", '"payload" BIGINT[]'), ("json", '"payload" TEXT')]
)
def test_nested_type_preview_uses_recorded_arrow_schema_without_generation(
    monkeypatch, capsys, mode, expected
):
    import pyarrow as pa

    from shape.cli import generation
    from shape.generation.engine import Engine
    from shape.generation.nested import NestedStrategy
    from shape.generation.schema import Column, GenSchema, Model, Table
    from shape.profile.nested import arrow_type_to_spec

    source = GenSchema(
        Model("nested", "nested"),
        {
            "t": Table(
                "t",
                {
                    "payload": Column(
                        "payload",
                        "nested",
                        {
                            "strategy": "nested",
                            "arrow_type_spec": arrow_type_to_spec(pa.list_(pa.int64())),
                        },
                    )
                },
            )
        },
    )
    monkeypatch.setattr(generation, "load_target", lambda *a: source)

    def no_generation(*args, **kwargs):
        pytest.fail("DDL preview must not generate values")

    monkeypatch.setattr(Engine, "generate", no_generation)
    monkeypatch.setattr(NestedStrategy, "generate", no_generation)
    assert (
        main(
            [
                "generate",
                "nested",
                "--dry-run",
                "--json",
                "--to",
                "postgresql://shape@localhost/db",
                "--sink-config",
                "postgres.nested=" + mode,
            ]
        )
        == 0
    )
    assert expected in json.loads(capsys.readouterr().out)["ddl"][0]["sql"]
