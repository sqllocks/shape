"""Issue #38: key material and passphrases never reach stdout, stderr, warnings or errors."""

from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import warnings

import pytest

from shape.artifact import read_model, sign_artifact, write_model
from shape.artifact.signing import load_private_key, write_keypair
from shape.security import credrefs

pytestmark = [pytest.mark.sign, pytest.mark.security]

PASS = "Zq9-unique-passphrase-5521"
WRONG = "Zq9-unique-WRONG-7788"


def _cli(*args, env=None, stdin=""):
    e = {k: v for k, v in os.environ.items() if k != "SHAPE_KEY_PASSPHRASE"}
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *args],
        capture_output=True,
        text=True,
        env=e,
        input=stdin,
    )


def _leaks(output: str, secrets: list[str]) -> list[str]:
    return [s for s in secrets if s and s in output]


def test_leak_check_detects_a_leak():
    # the guard itself: it must fail when a secret is present
    assert _leaks("... hunter2 ...", ["hunter2"]) == ["hunter2"]
    assert _leaks("clean", ["hunter2"]) == []


def _secrets(sk: bytes, priv_text: str) -> list[str]:
    body = "".join(line for line in priv_text.splitlines() if "-----" not in line)
    return [
        PASS,
        WRONG,
        base64.b64encode(sk).decode(),
        sk.hex(),
        body[:40],
    ]


def test_cli_never_prints_key_material_or_passphrases(tmp_path):
    out = []
    r = _cli("keygen", str(tmp_path / "k"), env={"SHAPE_KEY_PASSPHRASE": PASS})
    out.append(r.stdout + r.stderr)
    priv = tmp_path / "k.key"
    sk = load_private_key(priv, PASS)
    secrets = _secrets(sk, priv.read_text())

    csv = tmp_path / "d.csv"
    csv.write_text("a,b\n1,x\n")
    shape = tmp_path / "d.shape"
    out_of = lambda r: out.append(r.stdout + r.stderr)  # noqa: E731
    out_of(_cli("profile", str(csv), "-o", str(shape)))
    cases = [
        ("sign", str(shape), "--key", str(priv), "--passphrase-env", "P", {"P": PASS}, ""),
        ("sign", str(shape), "--key", str(priv), "--passphrase-env", "P", {"P": WRONG}, ""),
        ("sign", str(shape), "--key", str(priv), "--passphrase-stdin", {}, WRONG + "\n"),
        ("sign", str(shape), "--key", str(priv), "--passphrase-stdin", {}, PASS + "\n"),
        ("sign", str(shape), "--key", str(priv), None, {}, ""),
        ("sign", str(shape), "--key", "env://NOPE", None, {}, ""),
        ("sign", str(shape), "--key", "env://BADKEY", None, {"BADKEY": priv.read_text()}, ""),
        ("sign", str(shape), "--key", "-", None, {}, priv.read_text()),
        ("keygen", str(tmp_path / "k"), None, None, {"SHAPE_KEY_PASSPHRASE": PASS}, ""),
        ("keygen", str(tmp_path / "u"), "--no-passphrase", None, {}, ""),
    ]
    for c in cases:
        args = [x for x in c[:-2] if x is not None]
        out_of(_cli(*args, env=c[-2], stdin=c[-1]))
    out_of(_cli("inspect", str(shape)))
    out_of(_cli("inspect", str(shape), "--verify", str(tmp_path / "k.pub")))
    unencrypted = (tmp_path / "u.key").read_text().strip()
    secrets.append(unencrypted)

    text = "\n".join(out)
    assert _leaks(text, secrets) == []


def test_api_errors_and_warnings_do_not_carry_secrets(tmp_path):
    secrets = [PASS, WRONG]
    caught = []
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        priv, _ = write_keypair(tmp_path / "k", PASS)
        with pytest.warns(UserWarning):
            write_keypair(tmp_path / "u", unencrypted=True)
        p = tmp_path / "a.shape"
        write_model(p, {"tables": {}}, name="t")
        sign_artifact(p, load_private_key(priv, PASS))
        read_model(p)
    caught += [str(x.message) for x in w]
    sk = load_private_key(priv, PASS)
    secrets += [base64.b64encode(sk).decode(), sk.hex()]
    for fn in (
        lambda: load_private_key(priv, WRONG),
        lambda: load_private_key(priv),
        lambda: load_private_key("env://ISS38_UNSET"),
        lambda: load_private_key("kv://vault/k"),
    ):
        with pytest.raises(ValueError) as e:
            fn()
        caught.append(str(e.value) + repr(e.value) + repr(e.value.__cause__))
    credrefs.register_resolver("kv", lambda _: (_ for _ in ()).throw(RuntimeError(PASS)))
    try:
        with pytest.raises(ValueError) as e:
            load_private_key("kv://v/k")
        caught.append(str(e.value) + repr(e.value.__cause__) + repr(e.value.__context__))
    finally:
        credrefs.unregister_resolver("kv")
    assert _leaks("\n".join(caught), secrets) == []


def test_signature_info_and_artifact_hold_no_private_key(tmp_path):
    priv, _ = write_keypair(tmp_path / "k", PASS)
    sk = load_private_key(priv, PASS)
    p = tmp_path / "a.shape"
    write_model(p, {"tables": {}}, name="t")
    sign_artifact(p, sk)
    blob = p.read_bytes()
    assert sk not in blob and base64.b64encode(sk) not in blob and PASS.encode() not in blob
    info = json.dumps(read_model(p).signature)
    assert base64.b64encode(sk).decode() not in info
