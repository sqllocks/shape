"""Item 7: shape-only and shape-plus-vault generation."""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import random
from collections import Counter
from pathlib import Path

import pytest

from shape.cli.main import main as cli_main

RARE = "ZQXRARE-CATEGORY-91"
EMAIL = "planted.person@example.invalid"
SOURCE_DIGEST = "e39d41d50050cc6734c1a2999c674b755aaeae9b7bf3422498ddf9f193318bad"
# the bytes `shape generate --from gen.shape --seed 7 --rows 500 -f csv` wrote before the vault
# package existed (recorded from the tree without it): shape-only mode must keep writing them.
# W8-04b (#768) re-recorded it once (was cbcd93ba...): the normal draws behind the fitted columns
# use shape.kernel.pmath now; the old digest held on this tree before that change and nothing else.
SHAPE_ONLY_DIGEST = "564272992d402a3b714c7aa218e25724572d669ddb1a26a0bdd79f81be6db263"
WARNING = (
    "shape: warning: output generated with {vault} contains real values from the vault; "
    "treat it like the source data"
)


def run(capsys, *argv: str):
    code = cli_main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def workdir(tmp_path: Path, kek: bytes):
    rng = random.Random(7)
    src = tmp_path / "gen.csv"
    with open(src, "w", newline="") as fh:
        writer = csv.writer(fh)  # default dialect: the digest below is of these exact bytes
        writer.writerow(["id", "status", "score", "email"])
        for i in range(300):
            status = rng.choice(["paid"] * 5 + ["new"] * 3 + ["void"])
            score = round(rng.uniform(1, 90), 2)
            email = f"user{i}@example.com"
            if i == 11:
                status, score, email = RARE, 987654321.123, EMAIL
            writer.writerow([i, status, score, email])
    assert hashlib.sha256(src.read_bytes()).hexdigest() == SOURCE_DIGEST
    key = tmp_path / "kek.key"
    key.write_text(base64.b64encode(kek).decode() + "\n")
    if os.name == "posix":
        key.chmod(0o600)
    policy = tmp_path / "policy.json"
    policy.write_text(
        json.dumps(
            {
                "format": "shape-vault-policy",
                "version": 1,
                "default": "none",
                "columns": {"gen.status": "categories", "gen.score": "extremes"},
            }
        )
    )
    return tmp_path


def _profile(capsys, w: Path, *extra: str):
    code, out, err = run(
        capsys,
        "profile",
        str(w / "gen.csv"),
        "-o",
        str(w / "gen.shape"),
        "--vault",
        str(w / "gen.shapevault"),
        "--vault-policy",
        str(w / "policy.json"),
        "--kek",
        str(w / "kek.key"),
        *extra,
    )
    assert code == 0, err
    return out, err


def _gen(capsys, w: Path, name: str, *extra: str, rows: str = "500"):
    return run(
        capsys,
        "generate",
        "--from",
        str(w / "gen.shape"),
        "--seed",
        "7",
        "--rows",
        rows,
        "-f",
        "csv",
        "-o",
        str(w / name),
        *extra,
    )


def _read(w: Path, name: str) -> list[dict[str, str]]:
    with open(w / name / "gen.csv", newline="") as fh:
        return list(csv.DictReader(fh))


def test_shape_only_generation_is_byte_identical_to_before(capsys, workdir):
    run(capsys, "profile", str(workdir / "gen.csv"), "-o", str(workdir / "gen.shape"))
    code, _, _ = _gen(capsys, workdir, "plain")
    assert code == 0
    assert (
        hashlib.sha256((workdir / "plain" / "gen.csv").read_bytes()).hexdigest()
        == SHAPE_ONLY_DIGEST
    )


def test_a_profile_with_a_vault_generates_the_same_bytes_without_the_flag(capsys, workdir):
    _profile(capsys, workdir)
    code, _, err = _gen(capsys, workdir, "plain")
    assert code == 0 and "real values" not in err
    assert (
        hashlib.sha256((workdir / "plain" / "gen.csv").read_bytes()).hexdigest()
        == SHAPE_ONLY_DIGEST
    )


def test_vault_mode_draws_exact_categories_with_vaulted_shares(capsys, workdir, kek):
    _profile(capsys, workdir)
    from shape.vault.format import open_vault

    vaulted = open_vault((workdir / "gen.shapevault").read_bytes(), kek).columns["gen.status"]
    counts = dict(vaulted.payload["enum_values"])
    total = sum(counts.values())
    code, _, err = _gen(
        capsys,
        workdir,
        "v",
        "--vault",
        str(workdir / "gen.shapevault"),
        "--kek",
        str(workdir / "kek.key"),
        rows="100000",
    )
    assert code == 0, err
    got = Counter(r["status"] for r in _read(workdir, "v"))
    assert set(got) <= set(counts), "only vaulted categories"
    assert RARE in got
    for value, n in counts.items():
        assert abs(got[value] / 100000 - n / total) < 0.01, value


def test_vault_mode_uses_the_raw_extremes_as_bounds(capsys, workdir, kek):
    _profile(capsys, workdir)
    _gen(
        capsys,
        workdir,
        "v",
        "--vault",
        str(workdir / "gen.shapevault"),
        "--kek",
        str(workdir / "kek.key"),
    )
    scores = [float(r["score"]) for r in _read(workdir, "v")]
    assert min(scores) >= 1.0 and max(scores) <= 987654321.123


def test_warning_printed_once_and_mode_recorded(capsys, workdir):
    _profile(capsys, workdir)
    code, out, err = _gen(
        capsys,
        workdir,
        "v",
        "--vault",
        str(workdir / "gen.shapevault"),
        "--kek",
        str(workdir / "kek.key"),
        "--json",
    )
    assert code == 0
    assert err.count(WARNING.format(vault=str(workdir / "gen.shapevault"))) == 1
    doc = json.loads(out)
    assert doc["generation_mode"] == "shape+vault"
    assert doc["vault_id"] and doc["vaulted_columns"] == ["gen.score", "gen.status"]


def test_shape_only_json_says_shape(capsys, workdir):
    run(capsys, "profile", str(workdir / "gen.csv"), "-o", str(workdir / "gen.shape"))
    _, out, _ = _gen(capsys, workdir, "plain", "--json")
    assert json.loads(out)["generation_mode"] == "shape"


def test_dry_run_and_plan_mark_vaulted_columns(capsys, workdir):
    _profile(capsys, workdir)
    flags = ["--vault", str(workdir / "gen.shapevault"), "--kek", str(workdir / "kek.key")]
    code, out, _ = _gen(capsys, workdir, "d", "--dry-run", "--json", *flags)
    assert code == 0 and "vault" in out
    code, out, _ = run(capsys, "plan", str(workdir / "gen.shape"), *flags)
    doc = json.loads(out)
    vaulted = {i["evidence"] for i in doc["items"] if i["status"] == "vault"}
    assert "gen.status.enum_values" in vaulted and "gen.score.max_value" in vaulted
    code, out, _ = run(capsys, "plan", str(workdir / "gen.shape"), *flags, "--status", "vault")
    assert {i["status"] for i in json.loads(out)["items"]} == {"vault"}


def test_nothing_is_marked_vault_without_the_flags(capsys, workdir):
    _profile(capsys, workdir)
    _, out, _ = run(capsys, "plan", str(workdir / "gen.shape"))
    assert "vault" not in {i["status"] for i in json.loads(out)["items"]}


def test_a_vault_that_does_not_match_the_profile_is_refused_exit_1(capsys, workdir):
    _profile(capsys, workdir)
    v = workdir / "gen.shapevault"
    v.write_bytes(v.read_bytes() + b"\n")
    code, out, err = _gen(
        capsys, workdir, "v", "--vault", str(v), "--kek", str(workdir / "kek.key")
    )
    assert code == 1
    assert not (workdir / "v").exists()
    for text in (out, err):
        assert RARE not in text and EMAIL not in text


def test_wrong_kek_is_refused_exit_1_without_values(capsys, workdir, other_kek):
    _profile(capsys, workdir)
    wrong = workdir / "wrong.key"
    wrong.write_text(base64.b64encode(other_kek).decode())
    if os.name == "posix":
        wrong.chmod(0o600)
    code, out, err = _gen(
        capsys, workdir, "v", "--vault", str(workdir / "gen.shapevault"), "--kek", str(wrong)
    )
    assert code == 1 and not (workdir / "v").exists()
    assert RARE not in out + err


def test_vault_without_kek_or_kek_without_vault_is_exit_2(capsys, workdir):
    _profile(capsys, workdir)
    code, _, err = _gen(capsys, workdir, "v", "--vault", str(workdir / "gen.shapevault"))
    assert code == 2 and "--kek" in err
    code, _, err = _gen(capsys, workdir, "v", "--kek", str(workdir / "kek.key"))
    assert code == 2 and "--vault" in err


def test_vault_flags_need_from(capsys, workdir):
    code, _, err = run(
        capsys,
        "generate",
        "retail",
        "--vault",
        str(workdir / "x"),
        "--kek",
        str(workdir / "kek.key"),
    )
    assert code == 2 and "--from" in err


def test_verify_checks_the_profile_signature(capsys, workdir):
    from shape.artifact.signing import load_private_key, sign_artifact, write_keypair

    _profile(capsys, workdir)
    priv, pub = write_keypair(workdir / "sk", unencrypted=True)
    sign_artifact(workdir / "gen.shape", load_private_key(str(priv)))
    flags = ["--vault", str(workdir / "gen.shapevault"), "--kek", str(workdir / "kek.key")]
    code, _, err = _gen(capsys, workdir, "ok", *flags, "--verify", str(pub))
    assert code == 0, err
    _, other = write_keypair(workdir / "other", unencrypted=True)
    code, _, err = _gen(capsys, workdir, "bad", *flags, "--verify", str(other))
    assert code == 1 and not (workdir / "bad").exists()


def test_a_profile_vault_pair_written_before_signing_keeps_the_vault_hash_in_the_signature(
    capsys, workdir
):
    """Signing covers manifest.json, which carries the vault hash: swapping the vault fails."""
    from shape.artifact.signing import load_private_key, sign_artifact, write_keypair
    from shape.vault.format import seal_vault
    from shape.vault.kek import resolve_kek

    _profile(capsys, workdir)
    priv, pub = write_keypair(workdir / "sk", unencrypted=True)
    sign_artifact(workdir / "gen.shape", load_private_key(str(priv)))
    from shape.artifact.io import read_artifact

    manifest, _ = read_artifact(workdir / "gen.shape", notice=False)
    forged = workdir / "forged.shapevault"
    forged.write_bytes(
        seal_vault(
            {
                "gen.status": (
                    "categories",
                    {"dtype": "string", "rows": 1, "enum_values": [["x", 1]]},
                )
            },
            manifest["shape_content_id"],
            resolve_kek(str(workdir / "kek.key")),
        )
    )
    code, _, err = _gen(
        capsys,
        workdir,
        "f",
        "--vault",
        str(forged),
        "--kek",
        str(workdir / "kek.key"),
        "--verify",
        str(pub),
    )
    assert code == 1 and not (workdir / "f").exists()


def test_profile_command_vault_with_capture_full_is_exit_2(capsys, workdir):
    code, _, err = run(
        capsys,
        "profile",
        str(workdir / "gen.csv"),
        "-o",
        str(workdir / "gen.shape"),
        "--capture",
        "full",
        "--vault",
        str(workdir / "gen.shapevault"),
        "--vault-policy",
        str(workdir / "policy.json"),
        "--kek",
        str(workdir / "kek.key"),
    )
    assert code == 2 and "capture" in err
    assert not (workdir / "gen.shapevault").exists() and not (workdir / "gen.shape").exists()


def test_profile_command_policy_errors_exit_2(capsys, workdir):
    (workdir / "bad.json").write_text(
        json.dumps({"format": "shape-vault-policy", "version": 1, "columns": {"gen.nope": "all"}})
    )
    code, _, err = run(
        capsys,
        "profile",
        str(workdir / "gen.csv"),
        "-o",
        str(workdir / "gen.shape"),
        "--vault",
        str(workdir / "gen.shapevault"),
        "--vault-policy",
        str(workdir / "bad.json"),
        "--kek",
        str(workdir / "kek.key"),
    )
    assert code == 2 and "gen.nope" in err


def test_leak_planted_values_are_in_no_output_but_the_decrypted_vault(capsys, workdir, kek):
    (workdir / "policy.json").write_text(
        json.dumps({"format": "shape-vault-policy", "version": 1, "default": "all"})
    )
    out, err = _profile(capsys, workdir, "--json", str(workdir / "summary.json"))
    outputs = [out, err, (workdir / "summary.json").read_text()]
    outputs.append((workdir / "gen.shape").read_bytes().decode("latin-1"))
    outputs.append((workdir / "gen.shapevault").read_bytes().decode("latin-1"))
    for text in outputs:
        for needle in (RARE, EMAIL, "987654321"):
            assert needle not in text
    from shape.vault.format import open_vault

    opened = open_vault((workdir / "gen.shapevault").read_bytes(), kek)
    blob = json.dumps({n: c.payload for n, c in opened.columns.items()})
    assert RARE in blob and EMAIL in blob and "987654321.123" in blob
    # a failing verify prints no value either
    v = workdir / "gen.shapevault"
    v.write_bytes(v.read_bytes() + b" ")
    code, out, err = run(
        capsys,
        "vault",
        "verify",
        str(v),
        "--shape",
        str(workdir / "gen.shape"),
        "--kek",
        str(workdir / "kek.key"),
        "--json",
    )
    assert code == 1 and RARE not in out + err and EMAIL not in out + err


def test_two_vault_runs_with_one_seed_are_identical(capsys, workdir):
    _profile(capsys, workdir)
    flags = ["--vault", str(workdir / "gen.shapevault"), "--kek", str(workdir / "kek.key")]
    _gen(capsys, workdir, "a", *flags)
    _gen(capsys, workdir, "b", *flags)
    assert (workdir / "a" / "gen.csv").read_bytes() == (workdir / "b" / "gen.csv").read_bytes()
