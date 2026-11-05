"""Omitted design dimensions are resolved before the strict SQL sink validates."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

from shape.bridge.core import Bridge
from shape.builtins.sinks.sql import SqlSink
from shape.cli.main import main
from shape.design import DesignInput
from shape.design.ddl import DIALECTS, emit_ddl
from shape.design.engine import derive

MODES = ("3nf", "star", "snowflake")
_cli = cast(Callable[[list[str]], int], main)


def _doc(decimal: dict[str, Any], string: dict[str, Any]) -> dict[str, Any]:
    return {
        "format": "shape-design",
        "version": 1,
        "name": "defaults",
        "entities": [
            {
                "name": "customer",
                "attributes": [
                    {"name": "cid", "type": "integer"},
                    {"name": "label", "type": "string", **string},
                ],
                "keys": [["cid"]],
            },
            {
                "name": "sale",
                "attributes": [
                    {"name": "id", "type": "integer"},
                    {"name": "cid", "type": "integer", "references": "customer"},
                    {"name": "amount", "type": "decimal", **decimal},
                ],
                "keys": [["id"]],
            },
        ],
        "facts": [
            {
                "name": "sales",
                "source": "sale",
                "grain": ["id"],
                "degenerate": ["id"],
                "dimensions": [{"entity": "customer", "via": "cid"}],
                "measures": [{"name": "amount", "attribute": "amount", "additivity": "additive"}],
            }
        ],
    }


def _line(ddl: str, name: str) -> str:
    lines = [
        line
        for line in ddl.splitlines()
        if re.match(r"\s*(?:\[" + name + r'\]|"' + name + r'"|`' + name + r"`)\s+", line)
    ]
    assert len(lines) == 1, ddl
    return lines[0]


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    "decimal,dimensions",
    [
        ({}, (18, 2)),
        ({"precision": 12}, (12, 2)),
        ({"scale": 4}, (18, 4)),
        ({"scale": 0}, (18, 0)),
        ({"precision": 12, "scale": 0}, (12, 0)),
    ],
)
@pytest.mark.parametrize("string", [{}, {"max_length": 33}], ids=["unbounded", "explicit-width"])
def test_cli_and_bridge_resolve_only_omitted_design_dimensions(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    dialect: str,
    decimal: dict[str, Any],
    dimensions: tuple[int, int],
    string: dict[str, Any],
) -> None:
    doc = _doc(decimal, string)
    path = tmp_path / "design.json"
    path.write_text(json.dumps(doc))
    original = path.read_bytes()
    design = derive(DesignInput.from_dict(doc), mode)
    original_design = design.to_dict()
    canonical = emit_ddl(design, dialect)
    assert design.to_dict() == original_design
    prefix = "NUMERIC" if dialect == "postgres" else "DECIMAL"
    expected_decimal = f"{prefix}({dimensions[0]},{dimensions[1]})"
    assert expected_decimal in _line(canonical, "amount")
    bounded = "NVARCHAR" if dialect == "tsql" else "VARCHAR"
    expected_string = (
        f"{bounded}(33)"
        if string
        else {
            "tsql": "NVARCHAR(MAX)",
            "tsql-fabric-warehouse": "VARCHAR(MAX)",
            "postgres": "TEXT",
            "mysql": "TEXT",
        }[dialect]
    )
    assert expected_string in _line(canonical, "label")
    assert _cli(["design", str(path), "--mode", mode, "--dialect", dialect]) == 0
    assert capsys.readouterr().out == canonical
    for version in ("1.2", "1.3", "1.4"):
        bridge = Bridge(tmp_path / ("jobs-" + version))
        try:
            response = bridge.handle(
                {
                    "api_version": version,
                    "id": "defaults",
                    "command": "design",
                    "args": {"input": str(path), "mode": mode, "dialect": dialect},
                }
            )
            assert response["ok"] is True, response
            assert response["id"] == "defaults"
            ddl = response["result"]["ddl"]
            assert expected_decimal in _line(ddl, "amount")
            if version != "1.2":
                assert ddl == canonical
            else:
                assert (f"{bounded}(33)" if string else f"{bounded}(255)") in _line(ddl, "label")
        finally:
            bridge.close()
    assert path.read_bytes() == original


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize(
    "decimal,string",
    [
        ({"precision": 0}, {}),
        ({"precision": True}, {}),
        ({"precision": 1001}, {}),
        ({"scale": -1}, {}),
        ({"precision": 4, "scale": 5}, {}),
        ({}, {"max_length": 0}),
        ({}, {"max_length": True}),
        ({"precision": "18); DROP TABLE x;--"}, {}),
        ({"precision": None}, {}),
        ({}, {"max_length": None}),
    ],
)
def test_explicit_invalid_dimensions_fail_before_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    dialect: str,
    decimal: dict[str, Any],
    string: dict[str, Any],
) -> None:
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(_doc(decimal, string)))
    original = path.read_bytes()
    output = tmp_path / "invalid.sql"
    assert _cli(["design", str(path), "--mode", mode, "--dialect", dialect, "-o", str(output)]) == 2
    assert not output.exists()
    assert "CREATE TABLE" not in capsys.readouterr().out
    for version in ("1.2", "1.3", "1.4"):
        bridge = Bridge(tmp_path / ("jobs-" + version))
        try:
            response = bridge.handle(
                {
                    "api_version": version,
                    "id": "invalid",
                    "command": "design",
                    "args": {"input": str(path), "mode": mode, "dialect": dialect},
                }
            )
            assert response["ok"] is False, response
            expected_code = (
                "input.invalid_value" if decimal == {"precision": 1001} else "input.invalid_schema"
            )
            assert response["error"]["code"] == expected_code
            assert "result" not in response
        finally:
            bridge.close()
    assert path.read_bytes() == original


@pytest.mark.parametrize("dialect", DIALECTS)
@pytest.mark.parametrize("legacy", [False, True])
@pytest.mark.parametrize("metadata,missing", [({}, "precision"), ({"precision": 18}, "scale")])
def test_non_design_sql_still_requires_declared_decimal_dimensions(
    dialect: str, legacy: bool, metadata: dict[str, Any], missing: str
) -> None:
    fabric = dialect == "tsql-fabric-warehouse"
    with pytest.raises(ValueError, match="amount.*" + missing):
        SqlSink._ddl(
            "sale",
            "sale",
            pa.schema([pa.field("amount", pa.string())]),
            {"amount": {"type": "decimal", **metadata}},
            [],
            "tsql" if fabric else dialect,
            fabric,
            {},
            False,
            _legacy_design_types=legacy,
        )


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse", "mysql"])
@pytest.mark.parametrize("decimal", [{"precision": 1}, {"scale": 19}])
def test_partial_metadata_must_still_form_a_valid_decimal_pair(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    dialect: str,
    decimal: dict[str, Any],
) -> None:
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(_doc(decimal, {})))
    output = tmp_path / "partial.sql"
    assert _cli(["design", str(path), "--mode", mode, "--dialect", dialect, "-o", str(output)]) == 2
    assert "CREATE TABLE" not in capsys.readouterr().out
    assert not output.exists()
    for version in ("1.2", "1.3", "1.4"):
        bridge = Bridge(tmp_path / ("jobs-" + version))
        try:
            response = bridge.handle(
                {
                    "api_version": version,
                    "command": "design",
                    "args": {"input": str(path), "mode": mode, "dialect": dialect},
                }
            )
            assert response["ok"] is False
            assert response["error"]["code"] == "input.invalid_value"
            assert "invalid decimal precision/scale" in response["error"]["message"]
        finally:
            bridge.close()
