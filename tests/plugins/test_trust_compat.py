"""W1-18: compatibility of the persisted formats (plugin allow-list v1, plugin signature v1).

``data/capsule_v1`` is a time capsule: an allow-list in JSON and YAML, a trusted public key and a
signed distribution (the key is a test key, ``bytes(range(32))``). They were written by this
version and must keep loading and verifying on every later one. Do not regenerate them.
"""

from __future__ import annotations

import json
import re
import sys
from importlib import metadata
from pathlib import Path

import pytest

from shape.plugins import trust
from shape.plugins.host import PluginHost

pytestmark = pytest.mark.contract

CAPSULE = Path(__file__).parent / "data" / "capsule_v1"
SCHEMA = Path(trust.__file__).parents[1] / "schemas" / "plugin-allowlist-v1.schema.json"
TYPES = {"object": dict, "array": list, "string": str, "boolean": bool}


def validate(value, schema, root, path="$"):
    """The JSON Schema subset the allow-list schema uses."""
    if "$ref" in schema:
        node = root
        for part in schema["$ref"].lstrip("#/").split("/"):
            node = node[part]
        return validate(value, node, root, path)
    errors: list[str] = []
    if "type" in schema and not isinstance(value, TYPES[schema["type"]]):
        return [f"{path}: expected {schema['type']}"]
    if "const" in schema and (value != schema["const"] or isinstance(value, bool)):
        errors.append(f"{path}: expected {schema['const']!r}")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: pattern")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{path}: too short")
    if isinstance(value, dict):
        errors += [f"{path}: missing {k}" for k in schema.get("required", []) if k not in value]
        props = schema.get("properties", {})
        for k, v in value.items():
            if k in props:
                errors += validate(v, props[k], root, f"{path}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{path}: unexpected {k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errors += validate(v, schema["items"], root, f"{path}[{i}]")
    return errors


def schema_errors(doc):
    schema = json.loads(SCHEMA.read_text())
    return validate(doc, schema, schema)


@pytest.mark.parametrize("name", ["allowlist_v1.json", "allowlist_v1.yaml"])
def test_capsule_allowlist_loads_and_matches_the_schema(name):
    if name.endswith("yaml"):
        pytest.importorskip("yaml")
    path = CAPSULE / name
    al = trust.load_allowlist(path)
    assert al.require_signature and list(al.plugins) == ["capsule-plugin"]
    assert al.plugins["capsule-plugin"].version == "==1.0"
    assert len(al.trusted_keys) == 1
    doc = json.loads((CAPSULE / "allowlist_v1.json").read_text())
    assert schema_errors(doc) == []


def test_schema_rejects_what_the_loader_rejects():
    base = {"format": "shape-plugin-allowlist", "version": 1, "plugins": []}
    assert schema_errors(base) == []
    assert schema_errors({**base, "version": 2})
    assert schema_errors({**base, "extra": 1})
    assert schema_errors({**base, "plugins": [{"version": "==1"}]})
    assert schema_errors({**base, "plugins": [{"distribution": "x", "record_sha256": "zz"}]})
    assert schema_errors({**base, "trusted_keys": [{"key_id": "k"}]})


def test_capsule_signed_distribution_still_verifies():
    site = CAPSULE / "site"
    (dist,) = [d for d in metadata.distributions(path=[str(site)]) if d.name == "capsule-plugin"]
    key = trust.load_allowlist(CAPSULE / "allowlist_v1.json").trusted_keys
    files = trust.installed_files(dist)
    assert trust.verify_signature(files, key) == "56475aa75463474c"
    doc = json.loads(files.sig_bytes())
    assert doc["format"] == "shape-plugin-signature" and doc["version"] == 1
    assert doc["algorithm"] == "Ed25519" and set(doc) == {
        "format",
        "version",
        "algorithm",
        "key_id",
        "signature",
    }


def test_capsule_loads_through_the_host(monkeypatch):
    monkeypatch.syspath_prepend(str(CAPSULE / "site"))
    monkeypatch.delitem(sys.modules, "capsule_mod", raising=False)

    def eps():
        return [
            ep
            for ep in metadata.entry_points(group="shape.detectors")
            if ep.dist is not None and ep.dist.name == "capsule-plugin"
        ]

    host = PluginHost(entry_points=eps, allowlist=CAPSULE / "allowlist_v1.json")
    assert host.get("shape.detectors", "one").name == "one"


def test_a_newer_signature_version_is_refused_with_an_upgrade_message(tmp_path):
    (dist,) = [
        d
        for d in metadata.distributions(path=[str(CAPSULE / "site")])
        if d.name == "capsule-plugin"
    ]
    blob = json.loads(trust.installed_files(dist).sig_bytes())
    blob["version"] = 2
    with pytest.raises(trust.PluginTrustError, match="newer than this Shape reads"):
        trust._parse_signature(json.dumps(blob).encode(), "shape-plugin.sig")
