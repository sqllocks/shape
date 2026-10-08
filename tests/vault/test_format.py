"""Item 1: the vault file format, envelope encryption, tampering and interoperability."""

# ruff: noqa: I001, E402
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _data import COLUMNS, PLANTED

import base64
import json

import pytest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from shape import compat
from shape.artifact.canonical import canonical_json
from shape.vault import errors
from shape.vault.format import ALGORITHM, FORMAT, VERSION, inspect_vault, open_vault, seal_vault
from shape.vault.kek import kek_id


def _seal(kek, pid, columns=None):
    return seal_vault(columns or COLUMNS, pid, kek)


def test_header_declares_format_and_version(kek, profile_id):
    doc = json.loads(_seal(kek, profile_id))
    assert doc["format"] == FORMAT == "shape-vault"
    assert doc["version"] == VERSION == 1
    assert doc["algorithm"] == ALGORITHM == "AES-256-GCM"
    assert doc["profile_content_id"] == profile_id
    assert doc["kek_id"] == kek_id(kek)
    assert len(doc["vault_id"]) == 32 and int(doc["vault_id"], 16) >= 0  # 128 random bits
    assert set(doc["wrapped_key"]) == {"nonce", "ciphertext"}
    assert set(doc["columns"]) == set(COLUMNS)
    for name, entry in doc["columns"].items():
        assert set(entry) == {"policy", "nonce", "ciphertext"}
        assert entry["policy"] == COLUMNS[name][0]
    assert "shape_version" in doc and "min_shape_version" in doc
    assert compat.KINDS["vault"].format == "shape-vault"


def test_round_trip_recovers_every_payload(kek, profile_id):
    opened = open_vault(_seal(kek, profile_id), kek)
    assert {k: (v.policy, v.payload) for k, v in opened.columns.items()} == COLUMNS
    assert opened.profile_content_id == profile_id


def test_no_planted_value_in_the_clear(kek, profile_id):
    raw = _seal(kek, profile_id)
    for v in PLANTED.values():
        assert v.encode() not in raw
    assert b"paid" not in raw


def test_two_writes_differ_in_every_secret_part(kek, profile_id):
    a, b = json.loads(_seal(kek, profile_id)), json.loads(_seal(kek, profile_id))
    assert a["vault_id"] != b["vault_id"]
    assert a["wrapped_key"]["nonce"] != b["wrapped_key"]["nonce"]
    assert a["wrapped_key"]["ciphertext"] != b["wrapped_key"]["ciphertext"]
    for name in COLUMNS:
        assert a["columns"][name]["nonce"] != b["columns"][name]["nonce"]
        assert a["columns"][name]["ciphertext"] != b["columns"][name]["ciphertext"]


def test_inspect_needs_no_key_and_shows_header_only(kek, profile_id):
    info = inspect_vault(_seal(kek, profile_id))
    assert info["vault_id"] and info["profile_content_id"] == profile_id
    assert info["kek_id"] == kek_id(kek)
    cols = {c["column"]: c for c in info["columns"]}
    assert cols["orders.status"]["policy"] == "categories"
    assert cols["orders.status"]["ciphertext_bytes"] > 0
    assert "paid" not in json.dumps(info)


def test_wrong_kek_names_both_ids_and_no_key(kek, other_kek, profile_id):
    with pytest.raises(errors.KekMismatchError) as e:
        open_vault(_seal(kek, profile_id), other_kek)
    msg = str(e.value)
    assert kek_id(kek) in msg and kek_id(other_kek) in msg
    assert base64.b64encode(kek).decode() not in msg


def _flip(text: str) -> str:
    """Change one character of a base64 or hex string to another valid digit."""
    i = len(text) // 2
    return text[:i] + ("0" if text[i] != "0" else "1") + text[i + 1 :]


def _mutate(raw, path, fn=_flip):
    doc = json.loads(raw)
    node = doc
    for p in path[:-1]:
        node = node[p]
    node[path[-1]] = fn(node[path[-1]])
    return json.dumps(doc).encode()


TAMPER = [
    ("vault_id",),
    ("profile_content_id",),
    ("kek_id",),
    ("wrapped_key", "nonce"),
    ("wrapped_key", "ciphertext"),
    ("columns", "orders.status", "nonce"),
    ("columns", "orders.status", "ciphertext"),
    ("columns", "orders.amount", "ciphertext"),
    ("columns", "orders.email", "ciphertext"),
    ("columns", "orders.email", "nonce"),
]


@pytest.mark.parametrize("path", TAMPER, ids=lambda p: ".".join(p))
def test_one_changed_character_fails_authentication(kek, profile_id, path):
    raw = _mutate(_seal(kek, profile_id), path)
    with pytest.raises(errors.VaultMismatchError) as e:
        open_vault(raw, kek)
    for v in PLANTED.values():
        assert v not in str(e.value)


def test_changed_policy_fails(kek, profile_id):
    raw = _mutate(_seal(kek, profile_id), ("columns", "orders.status", "policy"), lambda _: "all")
    with pytest.raises(errors.VaultAuthenticationError):
        open_vault(raw, kek)


def test_swapped_column_ciphertexts_fail(kek, profile_id):
    doc = json.loads(_seal(kek, profile_id))
    a, b = doc["columns"]["orders.status"], doc["columns"]["orders.amount"]
    a["ciphertext"], b["ciphertext"] = b["ciphertext"], a["ciphertext"]
    a["nonce"], b["nonce"] = b["nonce"], a["nonce"]
    with pytest.raises(errors.VaultAuthenticationError):
        open_vault(json.dumps(doc).encode(), kek)


def test_renamed_column_fails(kek, profile_id):
    doc = json.loads(_seal(kek, profile_id))
    doc["columns"]["orders.renamed"] = doc["columns"].pop("orders.status")
    with pytest.raises(errors.VaultAuthenticationError):
        open_vault(json.dumps(doc).encode(), kek)


def test_dropped_column_fails(kek, profile_id):
    doc = json.loads(_seal(kek, profile_id))
    del doc["columns"]["orders.status"]
    with pytest.raises(errors.VaultAuthenticationError):
        open_vault(json.dumps(doc).encode(), kek)


def test_column_cannot_move_to_another_vault(kek, profile_id):
    one, two = json.loads(_seal(kek, profile_id)), json.loads(_seal(kek, profile_id))
    one["columns"]["orders.status"] = two["columns"]["orders.status"]
    with pytest.raises(errors.VaultAuthenticationError):
        open_vault(json.dumps(one).encode(), kek)


def test_vault_cannot_be_rebound_to_another_profile(kek, profile_id):
    raw = _mutate(_seal(kek, profile_id), ("profile_content_id",), lambda _: "cd" * 32)
    with pytest.raises(errors.VaultAuthenticationError):
        open_vault(raw, kek)


def test_truncated_ciphertext_fails(kek, profile_id):
    raw = _mutate(
        _seal(kek, profile_id), ("columns", "orders.status", "ciphertext"), lambda s: s[:-8]
    )
    with pytest.raises(errors.VaultMismatchError):
        open_vault(raw, kek)


def test_empty_vault_is_valid(kek, profile_id):
    assert open_vault(seal_vault({}, profile_id, kek), kek).columns == {}


def test_independent_decrypt_from_the_documented_format(kek, profile_id):
    """Only ``AESGCM`` and the header fields: the format is standard envelope encryption."""
    doc = json.loads(_seal(kek, profile_id))
    header = canonical_json(
        {
            "format": doc["format"],
            "version": doc["version"],
            "vault_id": doc["vault_id"],
            "profile_content_id": doc["profile_content_id"],
            "kek_id": doc["kek_id"],
            "columns": sorted(doc["columns"]),
        }
    )
    prefix = b"shape-vault-v1\x00"
    wrapped = doc["wrapped_key"]
    data_key = AESGCM(kek).decrypt(
        base64.b64decode(wrapped["nonce"]),
        base64.b64decode(wrapped["ciphertext"]),
        prefix + b"key\x00" + header,
    )
    assert len(data_key) == 32
    for name, entry in doc["columns"].items():
        aad = (
            prefix
            + b"column\x00"
            + header
            + b"\x00"
            + canonical_json({"name": name, "policy": entry["policy"]})
        )
        plain = AESGCM(data_key).decrypt(
            base64.b64decode(entry["nonce"]), base64.b64decode(entry["ciphertext"]), aad
        )
        assert json.loads(plain) == COLUMNS[name][1]
        # canonical JSON of the payload: sorted keys, no spaces
        assert plain == canonical_json(COLUMNS[name][1])


def test_float_values_are_typed_not_json_floats(kek, profile_id):
    from shape.vault.format import decode_value, encode_value

    for v in (1.5, float("inf"), -0.0, 10**30, "x", None, True):
        assert decode_value(encode_value(v)) == v or v != v
    cols = {"t.c": ("extremes", {"min": encode_value(2.25), "max": encode_value(9.5)})}
    out = open_vault(seal_vault(cols, profile_id, kek), kek)
    assert decode_value(out.columns["t.c"].payload["min"]) == 2.25
