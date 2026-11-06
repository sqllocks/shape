"""Bridge 1.5 executes selected tables and exposes a certifiable card verdict."""

from __future__ import annotations

import json

import pytest


def call(bridge, command, version="1.5", **args):
    return bridge.handle({"api_version": version, "command": command, "args": args})


def suite_file(tmp_path, scenario="clean_baseline"):
    path = tmp_path / "suite.json"
    path.write_text(json.dumps({"format": "shape-suite", "version": 1, "scenarios": [scenario]}))
    return str(path)


def test_selected_tables_are_used_without_generation(bridge, tmp_path, monkeypatch):
    from shape.cli.incremental import write_tables
    from shape.scenario.library.run import _generate, _schema

    generated = _generate(_schema("retail"), "tiny", 42)
    source = tmp_path / "selected"
    source.mkdir()
    write_tables(generated.tables, "parquet", source)
    monkeypatch.setattr("shape.scenario.library.run._generate", lambda *a: pytest.fail("generated"))
    response = call(
        bridge, "suite_run", suite=suite_file(tmp_path), name="selected", input=str(source)
    )
    assert response["ok"], response
    assert response["result"]["suite"] == "selected"
    assert response["result"]["scenarios"][0]["outcome"]["gates"]["schema_conformance"] is True
    assert sorted(source.glob("*.parquet"))


@pytest.mark.parametrize("version", ["1.2", "1.3", "1.4"])
def test_older_suite_requests_refuse_selected_input(bridge, tmp_path, version):
    response = call(bridge, "suite_run", version, suite="smoke", input=str(tmp_path))
    assert response["error"]["code"] == "usage.unknown_argument"


def test_selected_missing_input_and_drift_refuse_before_job(bridge, tmp_path):
    response = call(bridge, "suite_run", suite="smoke", input=str(tmp_path / "missing"))
    assert response["error"]["code"] == "input.not_found"
    response = call(
        bridge,
        "suite_run",
        suite=suite_file(tmp_path, "class_imbalance_shift"),
        input=str(tmp_path),
    )
    assert response["error"]["code"] == "input.invalid_value"
    assert "drift" in response["error"]["message"]


def test_selected_incomplete_input_is_refused(bridge, tmp_path):
    source = tmp_path / "selected"
    source.mkdir()
    (source / "wrong.csv").write_text("id\n1\n")
    response = call(bridge, "suite_run", suite=suite_file(tmp_path), input=str(source))
    assert response["error"]["code"] == "input.invalid_value"
    assert "missing tables" in response["error"]["message"]


def test_report_card_aliases_and_boolean_verdict(bridge, tmp_path):
    from data_1_2 import real_tables, synthetic_tables, write_tables

    source = write_tables(tmp_path / "real", real_tables())
    generated = write_tables(tmp_path / "generated", synthetic_tables())
    response = call(bridge, "report_card", source=str(source), path=str(generated), format="csv")
    assert response["ok"], response
    card = response["result"]
    assert card["passed"] is (card["overall"] == "pass")
    old = call(bridge, "report_card", "1.4", real=str(source), synthetic=str(generated))
    assert old["ok"] and "passed" not in old["result"]
    response = call(bridge, "report_card", "1.4", source=str(source), path=str(generated))
    assert response["error"]["code"] == "usage.unknown_argument"


def test_report_card_alias_request_schema_accepts_aliases():
    from jsonschema_lite import validate

    from shape.bridge.registry import COMMANDS
    from shape.bridge.schemas import command_request

    request = {
        "api_version": "1.5",
        "command": "report_card",
        "args": {"source": "real", "path": "gen"},
    }
    assert validate(request, command_request(COMMANDS["report_card"])) == []


@pytest.mark.parametrize("symbolic", [False, True])
def test_selected_input_cannot_be_overwritten(bridge, tmp_path, symbolic):
    from shape.cli.incremental import write_tables
    from shape.scenario.library.run import _generate, _schema

    source = tmp_path / "nulls_injected"
    source.mkdir()
    write_tables(_generate(_schema("retail"), "tiny", 42).tables, "parquet", source)
    before = {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}
    output = tmp_path
    if symbolic:
        output = tmp_path / "output"
        output.mkdir()
        (output / "nulls_injected").symlink_to(source, target_is_directory=True)
    response = call(
        bridge,
        "suite_run",
        suite=suite_file(tmp_path, "nulls_injected"),
        input=str(source),
        output_dir=str(output),
    )
    assert response["error"]["code"] == "input.invalid_value"
    assert before == {p.name: p.read_bytes() for p in source.iterdir() if p.is_file()}


def test_report_card_aliases_refuse_ambiguity_and_missing_args(bridge):
    from jsonschema_lite import validate

    from shape.bridge.registry import COMMANDS
    from shape.bridge.schemas import command_request

    schema = command_request(COMMANDS["report_card"])
    for request in [
        {"command": "report_card"},
        {"command": "report_card", "args": {"real": "a", "source": "b", "synthetic": "c"}},
    ]:
        assert validate(request, schema)
        assert bridge.handle({"api_version": "1.5", **request})["ok"] is False
