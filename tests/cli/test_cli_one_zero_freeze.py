"""Lock the approved machine interface without changing command behavior."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from shape.cli import errors, exitcodes, machine
from shape.cli.introspect import core_commands
from shape.cli.main import main

ROOT = Path(__file__).resolve().parents[2]
BASELINE = json.loads((ROOT / "docs/specs/freeze-1.0.json").read_text(encoding="utf-8"))


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def flag_surface() -> dict[str, object]:
    return {
        c.path: {
            flag: {"nargs": a.nargs, "dest": a.dest}
            for a in c.parser._actions
            for flag in a.option_strings
            if flag in ("--json", "--dry-run")
        }
        for c in core_commands()
    }


def native_surface() -> dict[str, list[str]]:
    return {
        "dry_run": sorted(machine.NATIVE_DRY_RUN),
        "dry_run_document": sorted(machine.NATIVE_DRY_RUN_DOCUMENT),
        "not_applicable": sorted(machine.NOT_APPLICABLE),
        "writers": sorted(machine.SPECS),
    }


def test_machine_flag_coverage_and_native_handlers_are_frozen() -> None:
    assert digest(flag_surface()) == BASELINE["cli"]["flags_sha256"], (
        "machine flags changed; revise the freeze document for a compatible addition"
    )
    assert native_surface() == BASELINE["cli"]["native"]


def test_exit_code_mappings_are_frozen() -> None:
    assert digest(exitcodes.CODES) == BASELINE["cli"]["exit_codes_sha256"]
    assert exitcodes.ALIASES == BASELINE["cli"]["exit_aliases"]
    assert errors.EXIT_INPUT_ERROR == 2
    assert errors.EXIT_PIPE_CLOSED == 141


@pytest.mark.parametrize("case", BASELINE["cli"]["envelopes"], ids=lambda c: c["id"])
def test_machine_envelope_vectors(case: dict) -> None:
    if case["kind"] == "result":
        actual = machine.envelope("freeze", case["code"], case["payload"], case.get("error"))
    else:
        actual = machine.dry_run_document("freeze", case["actions"], case.get("error"))
    assert actual == case["expected"]
    assert machine.check_document(actual) == actual["format"]


@pytest.mark.parametrize("fmt", ("shape-result", "shape-dry-run"))
@pytest.mark.parametrize("version", (None, True, 0, "1", 2))
def test_machine_reader_refuses_unfrozen_versions(fmt: str, version: object) -> None:
    with pytest.raises(ValueError):
        machine.check_document(
            {"format": fmt, "version": version, "command": "freeze", "exit_code": 0, "actions": []}
        )


def shape_of(value: object) -> object:
    """Keys and JSON types, independent of runtime values and JSON key order."""
    if isinstance(value, dict):
        return {k: shape_of(v) for k, v in value.items()}
    if isinstance(value, list):
        variants = {json.dumps(shape_of(v), sort_keys=True) for v in value}
        return [json.loads(v) for v in sorted(variants)]
    return type(value).__name__


@pytest.mark.parametrize("case", BASELINE["cli"]["emitted"], ids=lambda c: c["id"])
def test_emitted_json_shapes_and_exit_codes(case: dict, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    Path("input.csv").write_text("id,x\n1,a\n2,b\n", encoding="utf-8")
    assert main(case["argv"]) == case["code"]
    actual = json.loads(capsys.readouterr().out)
    assert shape_of(actual) == case["shape"]
    if "--dry-run" in case["argv"]:
        assert sorted(p.name for p in tmp_path.iterdir()) == ["input.csv"]


def test_server_and_version_keep_their_existing_flag_semantics() -> None:
    parsers = {c.path: c.parser for c in core_commands()}
    for path in ("version", "bridge"):
        flags = {f for a in parsers[path]._actions for f in a.option_strings}
        assert not flags & {"--json", "--dry-run"}
    flags = {f for a in parsers["bridge schema"]._actions for f in a.option_strings}
    assert {"--json", "--dry-run"} <= flags
