"""Authenticated column policy writes return metadata only and fail closed."""

import base64
import hashlib
import json

import pytest

from shape.artifact.canonical import canonical_json
from shape.bridge.core import Bridge
from shape.vault.format import open_vault, seal_vault


def setup(tmp_path, monkeypatch):
    key = bytes(range(32))
    monkeypatch.setenv("POLICY_KEY", base64.b64encode(key).decode())
    path = tmp_path / "people.shapevault"
    path.write_bytes(seal_vault({"people.email": ("all", {"categories": [["PRIVATE", 1]]})}, "ab", key))
    return path, key


def call(tmp_path, path, **extra):
    return Bridge(tmp_path / "jobs").handle({
        "api_version": "1.6", "command": "vault_policy",
        "args": {"vault": str(path), "kek": "env://POLICY_KEY", "column": "people.email", "policy": "none", **extra},
    })


def test_policy_write_authenticated_and_metadata_only(tmp_path, monkeypatch):
    path, key = setup(tmp_path, monkeypatch)
    result = call(tmp_path, path)
    assert result["ok"], result
    body = result["result"]
    assert body["before_policy"] == {"columns": {"people.email": "all"}}
    assert body["policy"] == {"columns": {"people.email": "none"}}
    assert body["policy_digest"] == hashlib.sha256(canonical_json(body["policy"])).hexdigest()
    assert body["key_reference"] == "env://POLICY_KEY"
    assert open_vault(path.read_bytes(), key).columns["people.email"].policy == "none"
    assert "PRIVATE" not in json.dumps(result) and base64.b64encode(key).decode() not in json.dumps(result)


@pytest.mark.parametrize("extra", [{"unknown": True}, {"policy": "PRIVATE"}, {"column": "missing"}, {"kek": "literal"}, {"classification": "PII"}])
def test_refusals_preserve_original(tmp_path, monkeypatch, extra):
    path, _ = setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    response = call(tmp_path, path, **extra)
    assert response["ok"] is False
    assert path.read_bytes() == before
    assert "PRIVATE" not in json.dumps(response) and "literal" not in json.dumps(response)


def test_old_minors_refuse_write_arguments(tmp_path, monkeypatch):
    path, _ = setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    for minor in (4, 5):
        response = Bridge(tmp_path / "jobs").handle({"api_version": f"1.{minor}", "command": "vault_policy", "args": {"vault": str(path), "policy": "none"}})
        assert response["error"]["code"] == "usage.unknown_argument"
    assert path.read_bytes() == before


def test_atomic_failure_preserves_original(tmp_path, monkeypatch):
    path, _ = setup(tmp_path, monkeypatch)
    before = path.read_bytes()
    monkeypatch.setattr("os.replace", lambda *a: (_ for _ in ()).throw(OSError("unavailable")))
    assert call(tmp_path, path)["ok"] is False
    assert path.read_bytes() == before
