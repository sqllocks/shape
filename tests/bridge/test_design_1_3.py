"""Bridge 1.3 unbounded DDL and earlier-minor presentation compatibility."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import vectors_lib as lib

from shape.bridge.core import Bridge
from shape.bridge.protocol import API_VERSION, parse_request
from shape.cli.main import main
from shape.design.ddl import emit_ddl
from shape.design.engine import derive
from shape.design.model import load_design


@pytest.mark.parametrize(
    "case_name,columns,unbounded,legacy",
    [
        ("star", ("month_name", "day_of_week_name"), "NVARCHAR(MAX)", "NVARCHAR(255)"),
        ("postgres-3nf", ("city", "country"), "TEXT", "VARCHAR(255)"),
    ],
)
def test_all_five_historical_columns_keep_old_minors_and_use_unbounded_1_3(
    tmp_path, capsys, case_name, columns, unbounded, legacy
):
    doc = json.loads((Path(__file__).parents[2] / "docs/bridge/vectors/design.json").read_text())
    directory = tmp_path / "work"
    jobs = lib.prepare(directory, doc)
    one = next(c for c in doc["cases"] if c["name"] == case_name)
    request = lib.substitute(one["request"], str(directory))
    bridge = Bridge(jobs)
    results = {}
    for version in ("1.1", "1.2", "1.3"):
        response = bridge.handle({**request, "api_version": version})
        assert response["ok"], response
        results[version] = response["result"]["ddl"]
    for column in columns:
        for version in ("1.1", "1.2", "1.3"):
            lines = [
                line
                for line in results[version].splitlines()
                if line.lstrip().startswith((f'"{column}" ', f"[{column}] "))
            ]
            assert len(lines) == (2 if case_name == "postgres-3nf" and column == "city" else 1)
            assert all((unbounded if version == "1.3" else legacy) in line for line in lines)
    args = request["args"]
    canonical = emit_ddl(
        derive(load_design(args["input"]), args.get("mode", "3nf")),
        args.get("dialect", "tsql"),
        schema_name=args.get("schema_name"),
        drop=bool(args.get("drop")),
    )
    assert canonical == results["1.3"]
    argv = [
        "design",
        args["input"],
        "--mode",
        args.get("mode", "3nf"),
        "--dialect",
        args.get("dialect", "tsql"),
    ]
    if args.get("schema_name"):
        argv += ["--schema-name", args["schema_name"]]
    if args.get("drop"):
        argv += ["--drop"]
    assert main(argv) == 0
    assert capsys.readouterr().out == canonical
    assert API_VERSION == "1.3"
    assert parse_request({"api_version": "1.3", "command": "design"}).minor == 3


# Each historical column is exercised independently, including the PostgreSQL string key.
COLUMNS = (
    ("dim_date", "month_name", "tsql"),
    ("dim_date", "day_of_week_name", "tsql"),
    ("city", "city", "postgres"),
    ("city_city", "city", "postgres"),
    ("city_city", "country", "postgres"),
)


@pytest.mark.parametrize("table,column,dialect", COLUMNS)
@pytest.mark.parametrize("width", [1, 255, 4000, 4001, 0, -1, True])
def test_each_column_explicit_width_boundaries(
    tmp_path, monkeypatch, capsys, table, column, dialect, width
):
    from shape.design.model import DesignError
    from shape.design.result import Column, SchemaDesign, Table

    design = SchemaDesign(
        "boundary", "3nf", (Table(table, "relation", (Column(column, max_length=width),)),)
    )
    monkeypatch.setattr("shape.design.engine.derive", lambda *_a, **_k: design)
    from data_1_1 import write_design

    path = write_design(tmp_path, "retail")
    request = {
        "api_version": "1.3",
        "command": "design",
        "args": {"input": str(path), "dialect": dialect},
    }
    response = Bridge(tmp_path / "jobs").handle(request)
    argv = ["design", str(path), "--dialect", dialect]
    if width in (0, -1) or isinstance(width, bool):
        assert not response["ok"] and column in response["error"]["message"]
        with pytest.raises(DesignError, match=column):
            emit_ddl(design, dialect)
        assert main(argv) == 2
        assert column in capsys.readouterr().err
    else:
        assert response["ok"], response
        expected = (
            ("NVARCHAR(MAX)" if width > 4000 else f"NVARCHAR({width})")
            if dialect == "tsql"
            else f"VARCHAR({width})"
        )
        assert expected in response["result"]["ddl"]
        assert emit_ddl(design, dialect) == response["result"]["ddl"]
        assert main(argv) == 0
        assert capsys.readouterr().out == response["result"]["ddl"]


@pytest.mark.parametrize("version", ["1.1", "1.2", "1.3"])
@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse"])
@pytest.mark.parametrize("table,column,_", COLUMNS)
def test_legacy_presentation_never_bypasses_named_indexed_refusal(
    tmp_path, monkeypatch, version, dialect, table, column, _
):
    import socket

    from data_1_1 import write_design

    from shape.design.result import Column, SchemaDesign, Table

    connections = []

    def refuse_connection(*args, **kwargs):
        connections.append((args, kwargs))
        raise AssertionError("SQL validation must precede every connection")

    monkeypatch.setattr(socket.socket, "connect", refuse_connection)
    design = SchemaDesign(
        "indexed", "3nf", (Table(table, "relation", (Column(column),), primary_key=(column,)),)
    )
    monkeypatch.setattr("shape.design.engine.derive", lambda *_a, **_k: design)
    path = write_design(tmp_path, "retail")
    response = Bridge(tmp_path / "jobs").handle(
        {
            "api_version": version,
            "command": "design",
            "args": {"input": str(path), "dialect": dialect},
        }
    )
    assert response["ok"] is False
    assert response["error"]["code"] == "input.invalid_value"
    assert (
        response["error"]["message"]
        == f"column {column!r}: indexed string requires explicit max_length"
    )

    assert connections == []
