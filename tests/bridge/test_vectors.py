"""P6-11 deliverable 6: a compatibility test per command. The published vectors
(``docs/bridge/vectors``) are requests with the responses a bridge must give; each is checked
against the published schemas and against a live bridge, so a result cannot change shape (or lose
a field) without these tests, and the committed schemas, changing."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import vectors_lib as lib
from fakes import FakeFabric
from jsonschema_lite import validate

from shape.bridge.core import Bridge
from shape.bridge.registry import COMMANDS

SCHEMAS = Path(__file__).resolve().parents[2] / "docs" / "bridge" / "schema"


def schema(rel: str):
    return json.loads((SCHEMAS / rel).read_text())


def test_there_is_a_vector_file_for_every_command_and_no_other():
    names = {p.stem for p in lib.VECTOR_DIR.glob("*.json")}
    assert names == set(COMMANDS)


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_every_command_has_a_success_and_a_failure_vector(command):
    doc = lib.load(command)
    assert (
        doc["format"] == "shape-bridge-vectors"
        and doc["version"] == 1
        and doc["command"] == command
    )
    outcomes = {c["response"]["ok"] for c in doc["cases"]}
    assert outcomes == {True, False}


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_the_vectors_agree_with_the_published_schemas(command):
    doc = lib.load(command)
    request_schema = schema(f"commands/{command}.request.schema.json")
    result_schema = schema(f"commands/{command}.result.schema.json")
    response_schema = schema("response.schema.json")
    for one in doc["cases"]:
        problems = validate(one["request"], request_schema)
        if one.get("valid_request", True):
            assert problems == [], (one["name"], problems)
        else:
            assert problems, f"{one['name']}: marked invalid but the schema accepts it"
        response = one["response"]
        assert validate(response, response_schema) == [], (one["name"], response)
        if response["ok"]:
            assert validate(response["result"], result_schema, wildcard=lib.ANY) == [], (
                one["name"],
                response,
            )


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_a_live_bridge_gives_the_responses_of_the_vectors(command, tmp_path, monkeypatch):
    import time

    doc = lib.load(command)
    directory = tmp_path / "work"
    jobs = lib.prepare(directory, doc)
    monkeypatch.setenv("SHAPE_FABRIC_STORAGE_TOKEN", "stor")
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    monkeypatch.setenv("SHAPE_HOME", str(directory / "shape-home"))
    bridge = Bridge(jobs)
    with lib.environment(doc):
        for setup in doc.get("setup", []):
            assert bridge.handle(lib.substitute(setup, str(directory)))["ok"]
        for one in doc["cases"]:
            request = lib.substitute(one["request"], str(directory))
            if one.get("needs") == "fabric":
                monkeypatch.setattr("shape.scale.http.urllib_transport", FakeFabric())
            actual = bridge.handle(request)
            expected = lib.substitute(one["response"], str(directory))
            assert lib.matches(expected, actual) == [], (command, one["name"], actual)
    deadline = time.time() + 60
    while time.time() < deadline and any(j["status"] == "running" for j in bridge.jobs.list()):
        time.sleep(0.05)
    assert os.path.isdir(directory)


def test_a_vector_recorded_without_scikit_learn_says_so():
    """INT-18: the report-card vectors were recorded without scikit-learn; CI installs it (extra
    ``advanced``), and then the adversarial test runs and the answer differs. A file whose answers
    depend on its absence declares ``needs: "no-advanced"``, and the replay hides it."""
    for path in lib.VECTOR_DIR.glob("*.json"):
        text = path.read_text()
        if "scikit-learn is not installed" in text:
            assert json.loads(text).get("needs") == lib.NO_ADVANCED, path


def test_the_vectors_do_not_contain_a_secret_or_a_machine_path():
    for path in lib.VECTOR_DIR.glob("*.json"):
        text = path.read_text()
        assert "/tmp/" not in text and "/home/" not in text and "/root/" not in text, path


def test_a_path_under_the_scratch_directory_is_compared_with_the_platform_separator():
    """A vector writes a path under ``${DIR}`` with ``/``; the bridge answers with the platform's
    own paths, so on Windows the expected path takes ``\\`` (the 1.0 and 1.1 replays too)."""
    win = "C:\\Users\\me\\work"
    doc = {
        "message": "file not found: ${DIR}/missing.json",
        "files": ["${DIR}/out/customer.csv"],
        "warning": "${DIR}/a.shape is not signed: its origin is not verified",
        "dir": "${DIR}",
    }
    assert lib.substitute(doc, win, sep="\\") == {
        "message": "file not found: C:\\Users\\me\\work\\missing.json",
        "files": ["C:\\Users\\me\\work\\out\\customer.csv"],
        "warning": "C:\\Users\\me\\work\\a.shape is not signed: its origin is not verified",
        "dir": win,
    }
    assert lib.substitute(doc, "/w", sep="/")["files"] == ["/w/out/customer.csv"]
    assert lib.abstract(lib.substitute(doc, win, sep="\\"), win, sep="\\") == doc
    assert lib.abstract(lib.substitute(doc, "/w", sep="/"), "/w", sep="/") == doc
