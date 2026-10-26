"""Database URI previews require the optional database plugin registrations."""

import pytest


@pytest.mark.parametrize("target", ["postgresql://unreachable/db", "mysql://unreachable/db"])
def test_database_dry_run_shows_constraints_without_connecting(capsys, target, tmp_path):
    import json

    from shape.cli.main import main
    from shape.generation.domains import load_domain

    document = load_domain("retail").schema.to_dict()
    for table in document["tables"].values():
        for column in table["columns"].values():
            if column["type"] == "decimal":
                column.update(precision=18, scale=2)
    schema = tmp_path / "retail-explicit.json"
    schema.write_text(json.dumps(document))
    assert main(["generate", str(schema), "--dry-run", "--to", target]) == 0
    output = capsys.readouterr().out
    assert "ALTER TABLE" in output and "FOREIGN KEY" in output


def test_duckdb_preview_uses_executable_parent_first_inline_constraints(capsys, tmp_path):
    import json

    import duckdb

    from shape.cli.main import main

    path = tmp_path / "preview.duckdb"
    assert (
        main(["generate", "retail", "--dry-run", "--json", "--to", "duckdb:///" + path.as_posix()])
        == 0
    )
    statements = json.loads(capsys.readouterr().out)["constraint_ddl"]
    assert statements and all(sql.startswith("CREATE TABLE") for sql in statements)
    assert any("FOREIGN KEY" in sql for sql in statements)
    with duckdb.connect() as con:
        for sql in statements:
            con.execute(sql)
    assert not path.exists()
