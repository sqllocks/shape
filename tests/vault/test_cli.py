"""Items 2, 5, 6 on the command line: exit codes, --json, and no secret in any output."""

from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys

import pytest

from shape.cli import vault as vault_cli
from shape.vault.kek import kek_id

PLAIN = ("paid", "planted.person@example.invalid", "ZQXRARE-CATEGORY-91", "987654321")


def run(capsys, *argv):
    code = vault_cli.main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def kek_ref(tmp_path, key: bytes, name="k.key") -> str:
    p = tmp_path / name
    p.write_text(base64.b64encode(key).decode() + "\n")
    if os.name == "posix":
        p.chmod(0o600)
    return str(p)


def test_keygen_writes_key_and_never_overwrites(capsys, tmp_path):
    path = tmp_path / "kek.key"
    code, out, _ = run(capsys, "keygen", "-o", str(path))
    assert code == 0 and "key id" in out
    raw = base64.b64decode(path.read_text().strip())
    assert len(raw) == 32 and kek_id(raw) in out
    assert base64.b64encode(raw).decode() not in out  # the key is never printed
    code, _, err = run(capsys, "keygen", "-o", str(path))
    assert code == 2 and "never overwrites" in err


def test_keygen_json(capsys, tmp_path):
    code, out, _ = run(capsys, "keygen", "-o", str(tmp_path / "k"), "--json")
    doc = json.loads(out)
    assert code == 0 and doc["ok"] and len(doc["kek_id"]) == 16


def test_inspect_prints_header_without_a_key(capsys, pair):
    _, vault = pair
    code, out, _ = run(capsys, "inspect", str(vault))
    assert code == 0 and "orders.status" in out and "categories" in out
    assert not any(p in out for p in PLAIN)
    code, out, _ = run(capsys, "inspect", str(vault), "--json")
    doc = json.loads(out)
    assert doc["columns"][0]["column"] and doc["kek_id"]


def test_inspect_malformed_exits_2(capsys, tmp_path):
    bad = tmp_path / "bad.shapevault"
    bad.write_text("nope")
    code, _, err = run(capsys, "inspect", str(bad))
    assert code == 2 and err.startswith("shape: error:")


def test_verify_exit_0_with_key(capsys, pair, tmp_path, kek):
    shape_path, vault = pair
    code, out, _ = run(
        capsys, "verify", str(vault), "--shape", str(shape_path), "--kek", kek_ref(tmp_path, kek)
    )
    assert code == 0 and "valid" in out


def test_verify_wrong_kek_exit_1_names_ids_prints_no_value(capsys, pair, tmp_path, other_kek, kek):
    shape_path, vault = pair
    code, out, err = run(
        capsys,
        "verify",
        str(vault),
        "--shape",
        str(shape_path),
        "--kek",
        kek_ref(tmp_path, other_kek),
    )
    assert code == 1
    assert kek_id(kek) in out and kek_id(other_kek) in out
    for text in (out, err):
        assert not any(p in text for p in PLAIN)
        assert base64.b64encode(kek).decode() not in text
        assert base64.b64encode(other_kek).decode() not in text


def test_verify_kek_not_32_bytes_exit_2(capsys, pair, tmp_path):
    shape_path, vault = pair
    code, _, err = run(
        capsys,
        "verify",
        str(vault),
        "--shape",
        str(shape_path),
        "--kek",
        kek_ref(tmp_path, b"short"),
    )
    assert code == 2 and "32 bytes" in err


@pytest.mark.skipif(os.name != "posix", reason="mode bits")
def test_verify_world_readable_kek_exit_2(capsys, pair, tmp_path, kek):
    shape_path, vault = pair
    ref = kek_ref(tmp_path, kek)
    os.chmod(ref, 0o644)
    code, _, err = run(capsys, "verify", str(vault), "--shape", str(shape_path), "--kek", ref)
    assert code == 2 and "accessible to other users" in err


def test_verify_literal_key_on_command_line_refused_without_echo(capsys, pair, kek):
    shape_path, vault = pair
    literal = base64.b64encode(kek).decode()
    code, out, err = run(capsys, "verify", str(vault), "--shape", str(shape_path), "--kek", literal)
    assert code == 2 and literal not in out + err


def test_verify_hash_mismatch_exit_1(capsys, pair):
    shape_path, vault = pair
    vault.write_bytes(vault.read_bytes() + b"\n")
    code, out, _ = run(capsys, "verify", str(vault), "--shape", str(shape_path))
    assert code == 1 and "FAIL sha256" in out


def test_verify_newer_version_exit_2_with_minimum_release(capsys, pair, tmp_path):
    shape_path, vault = pair
    doc = json.loads(vault.read_bytes())
    doc.update(version=2, min_shape_version="7.7.0")
    newer = tmp_path / "n.shapevault"
    newer.write_text(json.dumps(doc))
    code, _, err = run(capsys, "verify", str(newer), "--shape", str(shape_path))
    assert code == 2 and "7.7.0" in err


def test_verify_json_and_signature(capsys, pair, tmp_path, kek):
    from shape.artifact.signing import sign_artifact, write_keypair

    shape_path, vault = pair
    priv, pub = write_keypair(tmp_path / "sk", unencrypted=True)
    from shape.artifact.signing import load_private_key

    sign_artifact(shape_path, load_private_key(str(priv)))
    code, out, _ = run(
        capsys,
        "verify",
        str(vault),
        "--shape",
        str(shape_path),
        "--kek",
        kek_ref(tmp_path, kek),
        "--verify",
        str(pub),
        "--json",
    )
    doc = json.loads(out)
    assert code == 0 and doc["ok"] and {c["check"] for c in doc["checks"]} >= {"signature"}


def test_verify_signature_failure_exit_1(capsys, pair, tmp_path):
    from shape.artifact.signing import write_keypair

    shape_path, vault = pair
    _, pub = write_keypair(tmp_path / "sk", unencrypted=True)
    code, out, _ = run(
        capsys, "verify", str(vault), "--shape", str(shape_path), "--verify", str(pub)
    )
    assert code == 1 and "FAIL signature" in out


def test_rekey_command(capsys, pair, tmp_path, kek, other_kek):
    shape_path, vault = pair
    args = [
        "rekey",
        str(shape_path),
        str(vault),
        "--kek",
        kek_ref(tmp_path, kek),
        "--new-kek",
        kek_ref(tmp_path, other_kek, "new.key"),
        "--out-shape",
        str(tmp_path / "x2.shape"),
        "--out-vault",
        str(tmp_path / "v2.shapevault"),
    ]
    code, out, _ = run(capsys, *args, "--dry-run", "--json")
    doc = json.loads(out)
    assert code == 0 and doc["dry_run"] and len(doc["writes"]) == 2
    assert not (tmp_path / "x2.shape").exists()
    code, _, _ = run(capsys, *args)
    assert code == 0 and (tmp_path / "x2.shape").exists()
    code, out, _ = run(
        capsys,
        "verify",
        str(tmp_path / "v2.shapevault"),
        "--shape",
        str(tmp_path / "x2.shape"),
        "--kek",
        kek_ref(tmp_path, other_kek, "new.key"),
    )
    assert code == 0
    code, _, err = run(capsys, *args)  # outputs now exist: never overwritten
    assert code == 2 and "not overwritten" in err


def test_rekey_wrong_old_kek_exit_1(capsys, pair, tmp_path, other_kek):
    shape_path, vault = pair
    ref = kek_ref(tmp_path, other_kek)
    code, _, err = run(
        capsys,
        "rekey",
        str(shape_path),
        str(vault),
        "--kek",
        ref,
        "--new-kek",
        ref,
        "--out-shape",
        str(tmp_path / "x2.shape"),
        "--out-vault",
        str(tmp_path / "v2.shapevault"),
    )
    assert code == 1 and not (tmp_path / "x2.shape").exists()
    assert not any(p in err for p in PLAIN)


def test_rekey_signs_with_key_and_notices_otherwise(capsys, pair, tmp_path, kek, other_kek):
    from shape.artifact.signing import load_private_key, sign_artifact, write_keypair

    shape_path, vault = pair
    priv, pub = write_keypair(tmp_path / "sk", unencrypted=True)
    sign_artifact(shape_path, load_private_key(str(priv)))
    base = [
        "rekey",
        str(shape_path),
        str(vault),
        "--kek",
        kek_ref(tmp_path, kek),
        "--new-kek",
        kek_ref(tmp_path, other_kek, "new.key"),
    ]
    code, _, err = run(
        capsys,
        *base,
        "--out-shape",
        str(tmp_path / "a.shape"),
        "--out-vault",
        str(tmp_path / "a.v"),
    )
    assert code == 0 and "signature is dropped" in err
    code, _, err = run(
        capsys,
        *base,
        "--out-shape",
        str(tmp_path / "b.shape"),
        "--out-vault",
        str(tmp_path / "b.v"),
        "--key",
        str(priv),
    )
    assert code == 0 and "dropped" not in err
    code, _, _ = run(
        capsys,
        "verify",
        str(tmp_path / "b.v"),
        "--shape",
        str(tmp_path / "b.shape"),
        "--verify",
        str(pub),
    )
    assert code == 0


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_keygen_inside_unignored_work_tree_exit_2(capsys, tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    code, _, err = run(capsys, "keygen", "-o", str(tmp_path / "kek.key"))
    assert code == 2 and "kek.key" in err and ".gitignore" in err
    assert not (tmp_path / "kek.key").exists()


def test_the_shape_entry_point_routes_vault(tmp_path):
    out = subprocess.run(
        [sys.executable, "-m", "shape", "vault", "keygen", "-o", str(tmp_path / "k")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert out.returncode == 0 and (tmp_path / "k").exists()


TAMPER_PATHS = [
    ("vault_id",),
    ("profile_content_id",),
    ("kek_id",),
    ("wrapped_key", "nonce"),
    ("wrapped_key", "ciphertext"),
    ("columns", "orders.status", "nonce"),
    ("columns", "orders.status", "ciphertext"),
    ("columns", "orders.amount", "ciphertext"),
]


def _flip_one(text: str) -> str:
    i = len(text) // 2
    return text[:i] + ("0" if text[i] != "0" else "1") + text[i + 1 :]


@pytest.mark.parametrize("path", TAMPER_PATHS, ids=lambda p: ".".join(p))
def test_a_changed_part_exits_1_and_prints_no_value_even_when_the_profile_names_it(
    capsys, pair, tmp_path, kek, path
):
    """The hash check is not the only defence: a profile rewritten to name the changed vault still
    fails authentication (exit 1)."""
    from shape.vault.ops import attach_vault

    shape_path, vault = pair
    doc = json.loads(vault.read_bytes())
    node = doc
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = _flip_one(node[path[-1]])
    raw = json.dumps(doc).encode()
    changed = tmp_path / "changed.shapevault"
    changed.write_bytes(raw)
    # 1. the profile still names the original: hash mismatch
    code, out, err = run(capsys, "verify", str(changed), "--shape", str(shape_path))
    assert code == 1
    # 2. the profile now names the changed file (hash and id fixed up): authentication fails
    forged = tmp_path / "forged.shape"
    shutil.copy(shape_path, forged)
    if path != ("vault_id",) and path != ("profile_content_id",):
        attach_vault(forged, raw)
        code, out, err = run(
            capsys,
            "verify",
            str(changed),
            "--shape",
            str(forged),
            "--kek",
            kek_ref(tmp_path, kek),
        )
        assert code == 1
    for text in (out, err):
        assert not any(p in text for p in PLAIN)


def test_two_column_ciphertexts_swapped_exits_1(capsys, pair, tmp_path, kek):
    from shape.vault.ops import attach_vault

    shape_path, vault = pair
    doc = json.loads(vault.read_bytes())
    a, b = doc["columns"]["orders.status"], doc["columns"]["orders.amount"]
    a["ciphertext"], b["ciphertext"] = b["ciphertext"], a["ciphertext"]
    a["nonce"], b["nonce"] = b["nonce"], a["nonce"]
    swapped = tmp_path / "swapped.shapevault"
    swapped.write_text(json.dumps(doc))
    forged = tmp_path / "forged.shape"
    shutil.copy(shape_path, forged)
    attach_vault(forged, swapped.read_bytes())
    code, out, err = run(
        capsys, "verify", str(swapped), "--shape", str(forged), "--kek", kek_ref(tmp_path, kek)
    )
    assert code == 1 and not any(p in out + err for p in PLAIN)


def test_a_vault_from_another_profile_exits_1(capsys, pair, tmp_path, kek):
    from shape.vault.format import seal_vault

    shape_path, vault = pair
    other = tmp_path / "other.shapevault"
    other.write_bytes(seal_vault({"t.c": ("categories", {"x": 1})}, "cd" * 32, kek))
    code, out, err = run(
        capsys, "verify", str(other), "--shape", str(shape_path), "--kek", kek_ref(tmp_path, kek)
    )
    assert code == 1 and not any(p in out + err for p in PLAIN)


def test_profile_sign_covers_the_vault_hash(capsys, tmp_path, kek):
    """`shape profile --vault ... --sign KEY` signs after the vault is written, so the signature
    covers its hash; swapping the vault then fails `--verify`."""
    from shape.artifact.signing import write_keypair
    from shape.cli.main import main as shape_main
    from shape.vault.format import seal_vault
    from shape.vault.ops import write_file

    src = tmp_path / "t.csv"
    src.write_text("id,status\n" + "\n".join(f"{i},{'ab'[i % 2]}" for i in range(40)) + "\n")
    key = kek_ref(tmp_path, kek)
    policy = tmp_path / "p.json"
    policy.write_text(json.dumps({"format": "shape-vault-policy", "version": 1, "default": "all"}))
    priv, pub = write_keypair(tmp_path / "sk", unencrypted=True)
    code = shape_main(
        [
            "profile",
            str(src),
            "-o",
            str(tmp_path / "t.shape"),
            "--vault",
            str(tmp_path / "t.shapevault"),
            "--vault-policy",
            str(policy),
            "--kek",
            key,
            "--sign",
            str(priv),
        ]
    )
    capsys.readouterr()
    assert code == 0
    code, out, _ = run(
        capsys,
        "verify",
        str(tmp_path / "t.shapevault"),
        "--shape",
        str(tmp_path / "t.shape"),
        "--kek",
        key,
        "--verify",
        str(pub),
    )
    assert code == 0
    from shape.artifact.io import read_artifact

    manifest, _ = read_artifact(tmp_path / "t.shape", notice=False)
    other = tmp_path / "swap.shapevault"
    write_file(other, seal_vault({}, manifest["shape_content_id"], kek))
    code, _, _ = run(
        capsys,
        "verify",
        str(other),
        "--shape",
        str(tmp_path / "t.shape"),
        "--kek",
        key,
        "--verify",
        str(pub),
    )
    assert code == 1
