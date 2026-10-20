"""W9-03 dry-run DDL uses offline plugin hooks and generates no values."""

import json

import pytest

from shape.cli.main import main


@pytest.mark.parametrize(
    "target,needle",
    [
        ("mssql://localhost/shape", "DECIMAL(12,4)"),
        ("warehouse://localhost/shape", "DECIMAL(12,4)"),
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


@pytest.mark.parametrize(
    "generator",
    [
        {"strategy": "choice", "values": ["2026-01-01"]},
        {"strategy": "constant", "value": "2026-01-01"},
    ],
)
def test_wanted5_literal_strategy_preview_matches_actual_write(generator):
    import argparse

    from shape_fabric.sinks import SqlServerSink

    from shape.cli.generation import _target_ddl
    from shape.generation.engine import Engine
    from shape.generation.output import sql_options
    from shape.generation.schema import GenSchema

    schema = GenSchema.from_dict(
        {
            "schema_version": 1,
            "model": {"name": "t"},
            "tables": {
                "items": {
                    "name": "items",
                    "columns": {"at": {"name": "at", "type": "date", "generator": generator}},
                }
            },
        }
    )
    args = argparse.Namespace(
        to=["mssql://localhost/db"],
        format="summary",
        write_mode=None,
        roll_rows=None,
        roll_seconds=None,
        commit_rows=None,
        manifest=False,
    )
    preview = _target_ddl(args, schema)[0]["sql"]
    result = Engine(schema, row_counts={"items": 1}).generate()
    actual = SqlServerSink().ddl(
        "mssql://localhost/db",
        "items",
        result.tables["items"].schema,
        **sql_options(schema, "items"),
    )
    assert "NVARCHAR(MAX)" in actual
    assert preview == actual
