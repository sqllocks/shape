"""The 1.2 publication is immutable; replay every field except server version."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
import vectors_lib
from fakes import FakeFabric
from test_compat_1_0 import differences, schema_losses, substitute, without_version

from shape.bridge.core import Bridge
from shape.bridge.protocol import API_VERSION
from shape.bridge.schemas import all_schemas

ROOT = Path(__file__).parents[2] / "docs/bridge"


@pytest.mark.parametrize(
    "path", sorted((ROOT / "vectors/1.2").glob("*.json")), ids=lambda p: p.stem
)
def test_frozen_1_2_replay(path, tmp_path, monkeypatch):
    doc = json.loads(path.read_text())
    directory = tmp_path / "work"
    jobs = vectors_lib.prepare(directory, doc)
    monkeypatch.setenv("SHAPE_HOME", str(directory / "shape-home"))
    monkeypatch.setenv("SHAPE_FABRIC_STORAGE_TOKEN", "stor")
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    bridge = Bridge(jobs)
    with vectors_lib.environment(doc):
        for setup in doc.get("setup", []):
            assert bridge.handle(substitute(setup, str(directory)))["ok"]
        for one in doc["cases"]:
            if one.get("needs") == "fabric":
                monkeypatch.setattr("shape.scale.http.urllib_transport", FakeFabric())
            actual = bridge.handle(substitute(one["request"], str(directory)))
            expected = substitute(one["response"], str(directory))
            assert actual["api_version"] == API_VERSION == "1.4"
            # This 1.2 vector predates the additive registry classifications. Its
            # published matcher already allows these two fields; compare their
            # precise pre-existing shape, then retain exact comparison elsewhere.
            if path.stem == "registry_diff" and one["name"] == "raw-forms":
                for before, after in zip(
                    expected["result"]["changes"], actual["result"]["changes"], strict=True
                ):
                    assert set(after) - set(before) == {"class", "class_reason"}
                    assert isinstance(after["class"], str)
                    assert isinstance(after["class_reason"], str)
                assert vectors_lib.matches(without_version(expected), without_version(actual)) == []
            else:
                assert differences(without_version(expected), without_version(actual)) == [], one[
                    "name"
                ]
    deadline = time.time() + 60
    while time.time() < deadline and any(j["status"] == "running" for j in bridge.jobs.list()):
        time.sleep(0.05)


def test_frozen_1_2_schemas_are_preserved():
    current = all_schemas()
    for path in (ROOT / "schema/1.2").rglob("*.json"):
        relative = path.relative_to(ROOT / "schema/1.2").as_posix()
        if relative == "index.json":
            continue
        assert (
            schema_losses(
                json.loads(path.read_text()),
                current[relative],
                relative,
                request=relative.endswith("request.schema.json"),
            )
            == []
        ), relative
