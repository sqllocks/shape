"""W9-03 dry-run DDL uses offline plugin hooks and generates no values."""

import json

import pytest

from shape.cli.main import main


@pytest.mark.parametrize(
    "target,needle",
    [
        ("postgresql://shape@localhost/shape", "NUMERIC(12,4)"),
        ("mysql://shape@localhost/shape", "DECIMAL(12,4)"),
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


def test_wanted1_original_retail_without_decimal_dimensions_is_refused(tmp_path, capsys):
    from shape.generation.domains import load_domain

    original = load_domain("retail").schema
    assert original.tables["address"].columns["lat"].precision is None
    assert original.tables["address"].columns["lat"].scale is None
    code = main(["generate", "retail", "--to", "postgresql://shape@localhost/shape", "--dry-run"])
    assert code == 2
    assert "address.lat" in capsys.readouterr().err
    assert list(tmp_path.iterdir()) == []
