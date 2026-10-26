"""Vault management never exposes ciphertext, decrypted values or key material."""

from __future__ import annotations

import base64
import json

import pytest

from shape.bridge.core import Bridge
from shape.vault.format import inspect_vault, seal_vault
from shape.vault.ops import attach_vault


@pytest.fixture
def vault_pair(tmp_path, monkeypatch):
    import shape
    from shape.artifact.io import read_artifact

    source = tmp_path / "t.csv"
    source.write_text("id,status\n1,a\n2,b\n")
    profile = tmp_path / "t.shape"
    shape.save(shape.profile(str(source)), profile)
    manifest, _ = read_artifact(profile, notice=False)
    key = bytes(range(32))
    monkeypatch.setenv("VAULT_OLD", base64.b64encode(key).decode())
    monkeypatch.setenv("VAULT_NEW", base64.b64encode(bytes(range(32, 64))).decode())
    raw = seal_vault(
        {"t.status": ("categories", {"categories": [["PRIVATE_VALUE", 2]]})},
        manifest["shape_content_id"],
        key,
    )
    vault = tmp_path / "t.shapevault"
    vault.write_bytes(raw)
    attach_vault(profile, raw)
    return profile, vault, key


def call(tmp_path, command, **args):
    return Bridge(tmp_path / "jobs").handle(
        {
            "api_version": "1.4",
            "command": command,
            "args": {k: str(v) if hasattr(v, "__fspath__") else v for k, v in args.items()},
        }
    )


def test_status_and_verify_match_core_without_values(tmp_path, vault_pair):
    profile, vault, key = vault_pair
    status = call(tmp_path, "vault_status", vault=vault)
    assert status["ok"], status
    assert status["result"] == {"ok": True, **inspect_vault(vault.read_bytes())}
    verified = call(tmp_path, "vault_verify", vault=vault, shape=profile, kek="env://VAULT_OLD")
    assert verified["ok"] and verified["result"]["ok"], verified
    wrong = call(tmp_path, "vault_verify", vault=vault, shape=profile, kek="env://VAULT_NEW")
    assert wrong["ok"] and not wrong["result"]["ok"], wrong
    text = json.dumps([status, verified, wrong])
    assert "PRIVATE_VALUE" not in text and base64.b64encode(key).decode() not in text
    assert 'ciphertext"' not in text and "wrapped_key" not in text


def test_rewrap_dry_run_and_write(tmp_path, vault_pair, monkeypatch):
    profile, vault, _ = vault_pair
    new_shape, new_vault = tmp_path / "new.shape", tmp_path / "new.shapevault"
    args = dict(
        shape=profile,
        vault=vault,
        kek="env://VAULT_OLD",
        new_kek="env://VAULT_NEW",
        out_shape=new_shape,
        out_vault=new_vault,
    )
    planned = call(tmp_path, "vault_rewrap", **args, dry_run=True)
    assert planned["ok"] and planned["result"]["dry_run"], planned
    assert not new_shape.exists() and not new_vault.exists()
    monkeypatch.setattr("shape.vault.ops.ensure_git_ignored", lambda *_: None)
    written = call(tmp_path, "vault_rewrap", **args)
    assert written["ok"] and new_shape.exists() and new_vault.exists(), written
    assert call(tmp_path, "vault_verify", shape=new_shape, vault=new_vault, kek="env://VAULT_NEW")[
        "result"
    ]["ok"]
    assert not call(tmp_path, "vault_rewrap", **args)["ok"]
    assert "PRIVATE_VALUE" not in json.dumps([planned, written])


def test_policy_resolution_and_validation(tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "format": "shape-vault-policy",
                "version": 1,
                "default": "none",
                "by_classification": {"PII": "extremes"},
                "columns": {"t.status": "categories"},
            }
        )
    )
    response = call(
        tmp_path, "vault_policy", policy=policy, column="t.status", classification="PII"
    )
    assert response["ok"] and response["result"]["effective"] == "categories", response
    assert (
        call(tmp_path, "vault_policy", policy=policy, column="t.other", classification="PII")[
            "result"
        ]["effective"]
        == "extremes"
    )
    policy.write_text("{}")
    assert not call(tmp_path, "vault_policy", policy=policy)["ok"]


@pytest.mark.parametrize(
    "command", ["vault_status", "vault_verify", "vault_rewrap", "vault_policy"]
)
def test_commands_are_new_in_1_4(tmp_path, command):
    response = Bridge(tmp_path / "jobs").handle({"api_version": "1.3", "command": command})
    assert not response["ok"] and response["error"]["code"] == "usage.unknown_command"


def test_literal_key_is_never_echoed(tmp_path, vault_pair):
    profile, vault, key = vault_pair
    literal = base64.b64encode(key).decode()
    response = call(tmp_path, "vault_verify", vault=vault, shape=profile, kek=literal)
    assert not response["ok"] and literal not in json.dumps(response)


def test_bridge_owns_stdin_and_invalid_policy_errors_are_metadata_only(tmp_path, vault_pair):
    profile, vault, _ = vault_pair
    response = call(tmp_path, "vault_verify", vault=vault, shape=profile, verify="-")
    assert not response["ok"] and response["error"]["code"] == "input.invalid_value"
    response = call(
        tmp_path,
        "vault_rewrap",
        vault=vault,
        shape=profile,
        kek="env://VAULT_OLD",
        new_kek="env://VAULT_NEW",
        out_shape=tmp_path / "new.shape",
        out_vault=tmp_path / "new.shapevault",
        key="-",
    )
    assert not response["ok"] and response["error"]["code"] == "input.invalid_value"
    policy = tmp_path / "bad-policy.json"
    policy.write_text(
        json.dumps({"format": "shape-vault-policy", "version": 1, "default": "PRIVATE_VALUE"})
    )
    response = call(tmp_path, "vault_policy", policy=policy)
    assert not response["ok"] and "PRIVATE_VALUE" not in json.dumps(response)


@pytest.mark.parametrize("field", ["shape_version", "min_shape_version"])
@pytest.mark.parametrize(
    "value", [{"secret": "PRIVATE_VALUE"}, ["PRIVATE_VALUE"], "PRIVATE_VALUE", 123, True]
)
def test_status_rejects_unvalidated_release_metadata(tmp_path, vault_pair, field, value):
    _, vault, _ = vault_pair
    doc = json.loads(vault.read_bytes())
    doc[field] = value
    vault.write_text(json.dumps(doc))
    response = call(tmp_path, "vault_status", vault=vault)
    assert not response["ok"]
    assert response["error"]["code"] == "input.invalid_value"
    assert "PRIVATE_VALUE" not in json.dumps(response)


@pytest.mark.parametrize("command", ["vault_status", "vault_verify", "vault_rewrap"])
def test_malformed_vault_errors_never_echo_header_content(tmp_path, vault_pair, command):
    import hashlib

    from shape.artifact.io import read_artifact, write_artifact

    profile, vault, _ = vault_pair
    doc = json.loads(vault.read_bytes())
    doc["format"] = "PRIVATE_VALUE"
    vault.write_text(json.dumps(doc))
    manifest, components = read_artifact(profile, notice=False)
    manifest["vault"]["sha256"] = hashlib.sha256(vault.read_bytes()).hexdigest()
    write_artifact(profile, manifest, components)
    args = {"vault": vault}
    if command != "vault_status":
        args["shape"] = profile
    if command == "vault_rewrap":
        args.update(
            kek="env://VAULT_OLD",
            new_kek="env://VAULT_NEW",
            out_shape=tmp_path / "out.shape",
            out_vault=tmp_path / "out.shapevault",
            dry_run=True,
        )
    response = call(tmp_path, command, **args)
    assert not response["ok"]
    assert "PRIVATE_VALUE" not in json.dumps(response)


def test_newer_vault_version_preserves_safe_upgrade_guidance(tmp_path, vault_pair):
    _, vault, _ = vault_pair
    doc = json.loads(vault.read_bytes())
    doc.update(version=999, min_shape_version="9.9.0")
    vault.write_text(json.dumps(doc))
    response = call(tmp_path, "vault_status", vault=vault)
    assert not response["ok"]
    assert response["error"]["code"] == "input.unsupported_format_version"
    assert "9.9.0" in response["error"]["message"]
    assert "upgrade Shape" in response["error"]["hint"]


@pytest.mark.parametrize("command", ["vault_status", "vault_verify", "vault_rewrap"])
def test_missing_vault_has_a_platform_independent_error(tmp_path, vault_pair, command):
    profile, _, _ = vault_pair
    missing = tmp_path / "missing.shapevault"
    args = {"vault": missing}
    if command != "vault_status":
        args["shape"] = profile
    if command == "vault_rewrap":
        args.update(
            kek="env://VAULT_OLD",
            new_kek="env://VAULT_NEW",
            out_shape=tmp_path / "out.shape",
            out_vault=tmp_path / "out.shapevault",
            dry_run=True,
        )
    response = call(tmp_path, command, **args)
    assert not response["ok"]
    assert response["error"] == {
        "code": "input.not_found",
        "group": "input",
        "message": f"vault file not found: {missing}",
        "hint": None,
    }
