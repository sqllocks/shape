"""The catalogue describes every accepted corruption without running one."""

import json

import pytest

from shape.cli.main import main


def test_cli_catalogue_needs_no_input_or_output(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["chaos", "--list-kinds", "--json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["kinds"]) == 8
    assert list(tmp_path.iterdir()) == []


def test_bridge_catalogue_matches_cli(bridge, capsys):
    response = bridge.handle({"api_version": "1.4", "command": "chaos_kinds"})
    assert response["ok"], response
    assert main(["chaos", "--list-kinds", "--json"]) == 0
    cli = json.loads(capsys.readouterr().out)
    assert response["result"] == {key: cli[key] for key in ("surface", "syntax", "kinds")}


def test_catalogue_covers_parser_and_documents_rate_exceptions():
    from shape.chaos.catalogue import catalogue
    from shape.chaos.groundtruth import _OPTIONS, CORRUPTIONS, Corruption

    entries = catalogue()["kinds"]
    assert [entry["kind"] for entry in entries] == list(CORRUPTIONS)
    for entry in entries:
        assert set(entry["options"]) == {"from", "to", *_OPTIONS.get(entry["kind"], set())}
        c = Corruption(entry["kind"], column="value" if entry["column"] == "required" else None)
        assert entry["default_rate"] == c.rate
        assert entry["rate_semantics"]
    by_kind = {entry["kind"]: entry for entry in entries}
    assert "ignored" in by_kind["type_change"]["rate_semantics"]
    assert "step" in by_kind["null_creep"]["rate_semantics"]
    assert by_kind["duplicates"]["column"] == "forbidden"


@pytest.mark.parametrize("version", ["1.0", "1.1", "1.2", "1.3"])
def test_old_protocols_do_not_expose_catalogue(bridge, version):
    response = bridge.handle({"api_version": version, "command": "chaos_kinds"})
    assert response["error"]["code"] == "usage.unknown_command"
