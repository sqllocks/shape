"""Items 3 and 4: which values enter the vault, and writing it with the profile."""

from __future__ import annotations

import json
import os
import random
import shutil
import subprocess
from pathlib import Path

import pytest

import shape
from shape.artifact.io import read_artifact
from shape.vault import errors
from shape.vault.format import decode_value, inspect_vault, open_vault
from shape.vault.ops import vault_reference, verify_vault
from shape.vault.policy import parse_policy

RARE = "ZQXRARE-CATEGORY-91"
EMAIL = "planted.person@example.invalid"
EXTREME = 987654321.123


def _write_csv(path: Path) -> None:
    rng = random.Random(7)
    lines = ["id,status,score,email,qty"]
    for i in range(300):
        status = rng.choice(["paid"] * 5 + ["new"] * 3 + ["void"])
        score = round(rng.uniform(1, 90), 2)
        email = f"user{i}@example.com"
        if i == 11:
            status, score, email = RARE, EXTREME, EMAIL
        lines.append(f"{i},{status},{score},{email},{i % 4}")
    path.write_text("\n".join(lines) + "\n")


@pytest.fixture
def source(tmp_path: Path) -> Path:
    p = tmp_path / "orders.csv"
    _write_csv(p)
    return p


@pytest.fixture
def full(source: Path):
    return shape.profile(str(source))


def _policy(**kw):
    return parse_policy({"format": "shape-vault-policy", "version": 1, **kw})


def _save(full, tmp_path, kek, policy, **kw):
    out, vault = tmp_path / "orders.shape", tmp_path / "orders.shapevault"
    cid = shape.save(full, out, capture="safe", vault=vault, vault_policy=policy, kek=kek, **kw)
    return cid, out, vault


def test_save_writes_the_safe_capture_and_the_vault(full, tmp_path, kek):
    cid, out, vault = _save(full, tmp_path, kek, _policy(default="all"))
    manifest, _ = read_artifact(out, notice=False)
    ref = vault_reference(manifest)
    assert ref is not None and ref["vault_id"] == inspect_vault(vault.read_bytes())["vault_id"]
    assert manifest["capture"]["mode"] == "safe"
    assert manifest["shape_content_id"] == cid
    assert verify_vault(vault, out, kek=kek)["ok"]


def test_the_profile_alone_does_not_change_without_a_vault(full, tmp_path):
    """Without a vault the artifact has no vault field and is what W1-11 wrote."""
    out = tmp_path / "plain.shape"
    shape.save(full, out)
    manifest, _ = read_artifact(out, notice=False)
    assert "vault" not in manifest


def test_leak_none_of_the_planted_values_is_outside_the_vault(full, tmp_path, kek):
    _, out, vault = _save(full, tmp_path, kek, _policy(default="all"))
    for needle in (RARE, EMAIL, "987654321"):
        assert needle.encode() not in out.read_bytes()
        assert needle.encode() not in vault.read_bytes()


def test_the_planted_values_come_back_exactly_with_the_right_kek(full, tmp_path, kek):
    _, _, vault = _save(full, tmp_path, kek, _policy(default="all"))
    opened = open_vault(vault.read_bytes(), kek)
    status = opened.columns["orders.status"].payload
    assert RARE in {v for v, _ in status["value_counts_ext"]}
    assert dict(status["value_counts_ext"])["paid"] > dict(status["value_counts_ext"])[RARE]
    email = opened.columns["orders.email"].payload
    assert EMAIL in {v for v, _ in email["value_counts_ext"]}
    score = opened.columns["orders.score"].payload
    assert decode_value(score["max"][1]) == EXTREME


def test_only_columns_with_withheld_values_get_an_entry(full, tmp_path, kek):
    _, _, vault = _save(full, tmp_path, kek, _policy(default="all"))
    names = set(inspect_vault(vault.read_bytes())["columns"][i]["column"] for i in range(3))
    cols = {c["column"] for c in inspect_vault(vault.read_bytes())["columns"]}
    assert names <= cols
    # qty is an enum every category of which the safe capture releases in full: nothing to vault
    assert "orders.qty" not in cols
    assert {"orders.status", "orders.email", "orders.score"} <= cols


def test_policy_none_keeps_nothing(full, tmp_path, kek):
    _, out, vault = _save(full, tmp_path, kek, _policy(default="none"))
    assert inspect_vault(vault.read_bytes())["columns"] == []


def test_categories_keeps_no_extremes_and_extremes_keeps_no_categories(full, tmp_path, kek):
    policy = _policy(columns={"orders.status": "categories", "orders.score": "extremes"})
    _, _, vault = _save(full, tmp_path, kek, policy)
    cols = open_vault(vault.read_bytes(), kek).columns
    assert set(cols) == {"orders.status", "orders.score"}
    assert "min" not in cols["orders.status"].payload
    assert "value_counts_ext" in cols["orders.status"].payload
    assert set(cols["orders.score"].payload) >= {"min", "max"}
    assert "value_counts_ext" not in cols["orders.score"].payload
    assert cols["orders.status"].policy == "categories"


def test_precedence_explicit_over_classification_over_default(full, tmp_path, kek):
    policy = _policy(
        default="none",
        by_classification={"CONFIDENTIAL": "categories"},
        columns={"orders.email": "none"},
    )
    _, _, vault = _save(
        full,
        tmp_path,
        kek,
        policy,
        classifications={"email": "CONFIDENTIAL", "status": "CONFIDENTIAL", "score": "PUBLIC"},
    )
    cols = {c["column"] for c in inspect_vault(vault.read_bytes())["columns"]}
    assert cols == {"orders.status"}  # status by classification; email explicit none; score default


def test_a_policy_column_that_is_not_in_the_profile_is_input_error(full, tmp_path, kek):
    with pytest.raises(errors.VaultInputError, match=r"orders\.ghost"):
        _save(full, tmp_path, kek, _policy(columns={"orders.ghost": "all"}))
    assert (
        not (tmp_path / "orders.shape").exists() and not (tmp_path / "orders.shapevault").exists()
    )


def test_vault_with_capture_full_is_refused(full, tmp_path, kek):
    with pytest.raises(ValueError, match="capture full"):
        shape.save(
            full,
            tmp_path / "x.shape",
            capture="full",
            vault=tmp_path / "x.shapevault",
            vault_policy=_policy(default="all"),
            kek=kek,
        )
    assert not (tmp_path / "x.shape").exists() and not (tmp_path / "x.shapevault").exists()


def test_vault_needs_policy_and_kek(full, tmp_path, kek):
    with pytest.raises(ValueError, match="vault_policy"):
        shape.save(full, tmp_path / "x.shape", vault=tmp_path / "x.shapevault", kek=kek)
    with pytest.raises(ValueError, match="kek"):
        shape.save(
            full,
            tmp_path / "x.shape",
            vault=tmp_path / "x.shapevault",
            vault_policy=_policy(default="all"),
        )


def test_a_profile_captured_safe_has_no_values_for_a_vault(full, tmp_path, kek):
    from shape.privacy.redact import redact_profile

    safe = redact_profile(full)
    with pytest.raises(ValueError, match="captured safe"):
        shape.save(
            safe,
            tmp_path / "x.shape",
            vault=tmp_path / "x.shapevault",
            vault_policy=_policy(default="all"),
            kek=kek,
        )


def test_kek_may_be_a_reference(full, tmp_path, kek):
    import base64

    ref = tmp_path / "k.key"
    ref.write_text(base64.b64encode(kek).decode())
    if os.name == "posix":
        ref.chmod(0o600)
    _, out, vault = _save(full, tmp_path, str(ref), _policy(default="all"))
    assert verify_vault(vault, out, kek=kek)["ok"]


def test_two_writes_of_one_profile_differ(full, tmp_path, kek):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    _, _, va = _save(full, a, kek, _policy(default="all"))
    _, _, vb = _save(full, b, kek, _policy(default="all"))
    da, db = json.loads(va.read_bytes()), json.loads(vb.read_bytes())
    assert da["wrapped_key"] != db["wrapped_key"] and da["vault_id"] != db["vault_id"]
    for name in da["columns"]:
        assert da["columns"][name]["ciphertext"] != db["columns"][name]["ciphertext"]
        assert da["columns"][name]["nonce"] != db["columns"][name]["nonce"]


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_vault_in_unignored_work_tree_exit_2_and_nothing_written(full, tmp_path, kek):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    with pytest.raises(errors.VaultInputError, match=r"\*\.shapevault"):
        _save(full, tmp_path, kek, _policy(default="all"))
    assert not (tmp_path / "orders.shape").exists()
    assert not (tmp_path / "orders.shapevault").exists()
    (tmp_path / ".gitignore").write_text("*.shapevault\n")
    _save(full, tmp_path, kek, _policy(default="all"))


@pytest.mark.skipif(shutil.which("git") is None, reason="needs git")
def test_kek_file_in_unignored_work_tree_exit_2_and_nothing_written(full, tmp_path, kek):
    import base64

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / ".gitignore").write_text("*.shapevault\n")
    key = repo / "kek.key"
    key.write_text(base64.b64encode(kek).decode())
    if os.name == "posix":
        key.chmod(0o600)
    with pytest.raises(errors.VaultInputError, match="kek.key"):
        shape.save(
            full,
            repo / "o.shape",
            vault=repo / "o.shapevault",
            vault_policy=_policy(default="all"),
            kek=str(key),
        )
    assert not (repo / "o.shapevault").exists() and not (repo / "o.shape").exists()
    (repo / ".gitignore").write_text("*.shapevault\nkek.key\n")
    shape.save(
        full,
        repo / "o.shape",
        vault=repo / "o.shapevault",
        vault_policy=_policy(default="all"),
        kek=str(key),
    )


def test_git_setup_and_the_gitignore_line(tmp_path):
    import argparse

    from shape.cli import gitcmds

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    ns = argparse.Namespace(repo=str(tmp_path), pattern=None, command="shape cat")
    gitcmds.git_setup(ns)
    gitcmds.git_setup(ns)  # again: nothing changes
    lines = (tmp_path / ".gitignore").read_text().splitlines()
    assert lines.count("*.shapevault") == 1
    (tmp_path / ".gitignore").write_text("build/\n")
    gitcmds.git_setup(ns)
    assert (tmp_path / ".gitignore").read_text().splitlines() == ["build/", "*.shapevault"]


def test_a_multi_table_profile_gets_one_entry_per_withheld_column(tmp_path, kek):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    rng = random.Random(3)
    a.write_text(
        "id,kind\n"
        + "\n".join(f"{i},{rng.choice(['x'] * 8 + ['y'] * 3)}" for i in range(200))
        + "\nz1,RARE-A\n"
    )
    b.write_text("id,tag\n" + "\n".join(f"{i},{'t' if i % 2 else 'u'}" for i in range(200)) + "\n")
    prof = shape.profile({"a": str(a), "b": str(b)})
    out, vault = tmp_path / "m.shape", tmp_path / "m.shapevault"
    shape.save(prof, out, vault=vault, vault_policy=_policy(default="categories"), kek=kek)
    cols = {c["column"] for c in inspect_vault(vault.read_bytes())["columns"]}
    assert "a.kind" in cols and "b.tag" not in cols  # b.tag is released in full: nothing to vault
    assert RARE_A.encode() not in out.read_bytes()
    assert RARE_A.encode() not in vault.read_bytes()
    payload = open_vault(vault.read_bytes(), kek).columns["a.kind"].payload
    assert RARE_A in {v for v, _ in payload["enum_values"] + payload["value_counts_ext"]}


RARE_A = "RARE-A"
