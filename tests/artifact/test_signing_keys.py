"""Issue #38: key sources, encrypted private keys, and the "not verified" notice."""

from __future__ import annotations

import base64
import io
import json
import os
import stat
import subprocess
import sys
import warnings
import zipfile

import pytest

from shape.artifact import (
    ArtifactNotVerifiedWarning,
    ArtifactSignatureError,
    read_artifact,
    read_model,
    read_shape,
    sign_artifact,
    verify_artifact,
    write_model,
)
from shape.artifact.io import set_notice_handler
from shape.artifact.keys import (
    PassphraseRequired,
    UnencryptedKeyWarning,
    load_private_key,
    load_public_key,
    read_passphrase,
)
from shape.artifact.signing import generate_keypair, key_id, write_keypair
from shape.security import credrefs

pytestmark = pytest.mark.sign

PASS = "correct horse battery staple 7731"


def _make(tmp_path, name="a.shape"):
    p = tmp_path / name
    write_model(p, {"tables": {}}, name="t")
    return p


def _cli(*args, env=None, stdin=None):
    e = {k: v for k, v in os.environ.items() if k != "SHAPE_KEY_PASSPHRASE"}
    e.update(env or {})
    return subprocess.run(
        [sys.executable, "-m", "shape.cli.main", *args],
        capture_output=True,
        text=True,
        env=e,
        input=stdin if stdin is not None else "",
    )


# ---- credential references -------------------------------------------------------------------


def test_env_reference(monkeypatch):
    monkeypatch.setenv("ISS38_SECRET", "abc")
    assert credrefs.resolve_reference("env://ISS38_SECRET") == "abc"
    monkeypatch.delenv("ISS38_SECRET")
    with pytest.raises(credrefs.CredentialReferenceError, match="ISS38_SECRET is not set"):
        credrefs.resolve_reference("env://ISS38_SECRET")


def test_file_reference_strips_one_trailing_newline(tmp_path):
    f = tmp_path / "s.txt"
    f.write_text("value\n")
    f.chmod(0o600)  # a secret file others can read is refused (P6-07b)
    assert credrefs.resolve_reference(f"file://{f}") == "value"
    with pytest.raises(credrefs.CredentialReferenceError, match="cannot read"):
        credrefs.resolve_reference(f"file://{tmp_path}/missing")


def test_kv_needs_a_registered_resolver_and_takes_a_fake(monkeypatch):
    # no resolver is provided by an installed package here (the Fabric plugin provides one)
    monkeypatch.setattr(credrefs, "_LOADED", {})
    monkeypatch.setattr(credrefs, "_PROVIDERS", {})
    with pytest.raises(credrefs.CredentialReferenceError, match="no cloud SDK"):
        credrefs.resolve_reference("kv://vault/name")
    seen = []
    credrefs.register_resolver("kv", lambda rest: seen.append(rest) or "from-store")
    try:
        assert credrefs.resolve_reference("kv://vault/name") == "from-store"
        assert seen == ["vault/name"]
    finally:
        credrefs.unregister_resolver("kv")
    with pytest.raises(credrefs.CredentialReferenceError):
        credrefs.resolve_reference("kv://vault/name")


def test_resolver_failure_text_is_not_passed_on():
    def boom(_):
        raise RuntimeError("token=SUPERSECRET")

    credrefs.register_resolver("kv", boom)
    try:
        with pytest.raises(credrefs.CredentialReferenceError) as e:
            credrefs.resolve_reference("kv://v/n")
        assert "SUPERSECRET" not in str(e.value)
    finally:
        credrefs.unregister_resolver("kv")


def test_core_does_not_import_a_cloud_sdk():
    out = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, shape.artifact.signing, shape.security.credrefs;"
            "print([m for m in sys.modules if m.split('.')[0] in ('azure','boto3','botocore')])",
        ],
        capture_output=True,
        text=True,
    )
    assert out.stdout.strip() == "[]", out.stdout + out.stderr


# ---- key sources ------------------------------------------------------------------------------


def _unencrypted_pair(tmp_path, name="u"):
    with pytest.warns(UnencryptedKeyWarning):
        return write_keypair(tmp_path / name, unencrypted=True)


def test_private_key_from_stdin_env_file_and_kv(tmp_path, monkeypatch):
    priv, pub = _unencrypted_pair(tmp_path)
    expected = load_private_key(priv)
    text = priv.read_text()
    assert load_private_key("-", stdin=io.StringIO(text)) == expected
    monkeypatch.setenv("ISS38_KEY", text)
    assert load_private_key("env://ISS38_KEY") == expected
    assert load_private_key(f"file://{priv}") == expected
    credrefs.register_resolver("kv", lambda rest: text)
    try:
        assert load_private_key("kv://vault/signing-key") == expected
    finally:
        credrefs.unregister_resolver("kv")
    assert load_public_key("-", stdin=io.StringIO(pub.read_text())) == load_public_key(pub)
    monkeypatch.setenv("ISS38_PUB", pub.read_text())
    assert load_public_key("env://ISS38_PUB") == load_public_key(pub)


def test_cli_sign_with_key_from_env_stdin_file(tmp_path):
    priv, pub = _unencrypted_pair(tmp_path)
    text = priv.read_text()
    for src, kw in (
        ("env://ISS38_KEY", {"env": {"ISS38_KEY": text}}),
        ("-", {"stdin": text}),
        (f"file://{priv}", {}),
    ):
        p = _make(tmp_path, "x.shape")
        r = _cli("sign", str(p), "--key", src, **kw)
        assert r.returncode == 0, r.stderr
        assert _cli("verify", str(p), "--key", str(pub)).returncode == 0
        assert json.loads(r.stdout)["key_id"] == key_id(load_public_key(pub))


def test_cli_verify_key_from_env(tmp_path):
    priv, pub = _unencrypted_pair(tmp_path)
    p = _make(tmp_path)
    sign_artifact(p, load_private_key(priv))
    r = _cli("inspect", str(p), "--verify", "env://PUBK", env={"PUBK": pub.read_text()})
    assert r.returncode == 0, r.stderr
    r = _cli("inspect", str(p), "--verify", "env://PUBK")  # unset -> input error, not a pass
    assert r.returncode == 2 and "PUBK" in r.stderr


def test_cli_kv_without_resolver_is_a_clear_error(tmp_path):
    p = _make(tmp_path)
    r = _cli("sign", str(p), "--key", "kv://vault/k")
    assert r.returncode == 2
    # without a resolver package the message says so; with the Fabric plugin installed, the
    # resolver runs and reports what it lacks (a sign-in); either way the reference is named
    assert "kv://" in r.stderr


def test_key_and_passphrase_cannot_both_use_stdin(tmp_path):
    p = _make(tmp_path)
    r = _cli("sign", str(p), "--key", "-", "--passphrase-stdin", stdin="x\n")
    assert r.returncode == 2 and "standard input" in r.stderr


# ---- encrypted keys ---------------------------------------------------------------------------


def test_keygen_writes_an_encrypted_pkcs8_key_by_default(tmp_path):
    priv, pub = write_keypair(tmp_path / "k", PASS)
    text = priv.read_text()
    assert text.startswith("-----BEGIN ENCRYPTED PRIVATE KEY-----")
    if os.name == "posix":
        assert stat.S_IMODE(priv.stat().st_mode) == 0o600
    sk = load_private_key(priv, PASS)
    assert load_private_key(priv, lambda: PASS.encode()) == sk
    p = _make(tmp_path)
    assert sign_artifact(p, sk) == key_id(load_public_key(pub))
    assert verify_artifact(p, load_public_key(pub))["verified"] is True


def test_encrypted_key_is_standard_pkcs8(tmp_path):
    from cryptography.hazmat.primitives import serialization

    priv, _ = write_keypair(tmp_path / "k", PASS)
    key = serialization.load_pem_private_key(priv.read_bytes(), PASS.encode())
    raw = key.private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    assert raw == load_private_key(priv, PASS)


def test_wrong_or_missing_passphrase_fails_closed(tmp_path):
    priv, _ = write_keypair(tmp_path / "k", PASS)
    with pytest.raises(PassphraseRequired):
        load_private_key(priv)
    with pytest.raises(PassphraseRequired):
        load_private_key(priv, lambda: None)
    with pytest.raises(ValueError, match="wrong passphrase"):
        load_private_key(priv, "not the passphrase")


def test_unencrypted_key_never_asks_for_a_passphrase(tmp_path):
    priv, _ = _unencrypted_pair(tmp_path)

    def must_not_be_called():
        raise AssertionError("asked for a passphrase")

    load_private_key(priv, must_not_be_called)


def test_unencrypted_key_must_be_asked_for_and_warns(tmp_path):
    with pytest.raises(ValueError, match="passphrase is required"):
        write_keypair(tmp_path / "k")
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError):
        write_keypair(tmp_path / "k", PASS, unencrypted=True)
    with pytest.warns(UnencryptedKeyWarning, match="UNENCRYPTED"):
        write_keypair(tmp_path / "k", unencrypted=True)


def test_keygen_refuses_to_overwrite_with_a_friendly_error(tmp_path):
    write_keypair(tmp_path / "k", PASS)
    with pytest.raises(FileExistsError, match="refusing to overwrite"):
        write_keypair(tmp_path / "k", PASS)
    r = _cli("keygen", str(tmp_path / "k"), env={"SHAPE_KEY_PASSPHRASE": PASS})
    assert r.returncode == 2
    assert "refusing to overwrite" in r.stderr and "Errno" not in r.stderr
    # an existing public key alone also stops it and leaves no private key behind
    (tmp_path / "j.pub").write_text("x")
    with pytest.raises(FileExistsError):
        write_keypair(tmp_path / "j", PASS)
    assert not (tmp_path / "j.key").exists()


def test_cli_keygen_default_is_encrypted_and_needs_a_passphrase(tmp_path):
    r = _cli("keygen", str(tmp_path / "a"))  # no tty, no env, no stdin flag
    assert r.returncode == 2 and "passphrase" in r.stderr
    assert not (tmp_path / "a.key").exists()
    r = _cli("keygen", str(tmp_path / "b"), env={"SHAPE_KEY_PASSPHRASE": PASS})
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["encrypted"] is True
    assert (tmp_path / "b.key").read_text().startswith("-----BEGIN ENCRYPTED PRIVATE KEY-----")
    r = _cli("keygen", str(tmp_path / "c"), "--passphrase-env", "MYPASS", env={"MYPASS": PASS})
    assert r.returncode == 0 and json.loads(r.stdout)["encrypted"] is True
    r = _cli("keygen", str(tmp_path / "d"), "--passphrase-stdin", stdin=PASS + "\n")
    assert r.returncode == 0 and json.loads(r.stdout)["encrypted"] is True


def test_cli_keygen_no_passphrase_warns_loudly(tmp_path):
    r = _cli("keygen", str(tmp_path / "u"), "--no-passphrase")
    assert r.returncode == 0
    assert "WARNING" in r.stderr and "UNENCRYPTED" in r.stderr
    assert json.loads(r.stdout)["encrypted"] is False
    r = _cli(
        "keygen", str(tmp_path / "v"), "--no-passphrase", "--passphrase-env", "X", env={"X": "y"}
    )
    assert r.returncode == 2


def test_cli_sign_encrypted_key_passphrase_sources(tmp_path):
    priv, pub = write_keypair(tmp_path / "k", PASS)
    for kw, extra in (
        ({"env": {"SHAPE_KEY_PASSPHRASE": PASS}}, []),
        ({"env": {"MYPASS": PASS}}, ["--passphrase-env", "MYPASS"]),
        ({"stdin": PASS + "\n"}, ["--passphrase-stdin"]),
    ):
        p = _make(tmp_path, "x.shape")
        r = _cli("sign", str(p), "--key", str(priv), *extra, **kw)
        assert r.returncode == 0, r.stderr
        assert _cli("verify", str(p), "--key", str(pub)).returncode == 0
    p = _make(tmp_path, "y.shape")
    r = _cli("sign", str(p), "--key", str(priv))
    assert r.returncode == 2 and "passphrase" in r.stderr
    r = _cli("sign", str(p), "--key", str(priv), env={"SHAPE_KEY_PASSPHRASE": "wrong"})
    assert r.returncode == 2 and "wrong passphrase" in r.stderr
    # profile --sign takes the same options
    csv = tmp_path / "d.csv"
    csv.write_text("a,b\n1,x\n2,y\n")
    out = tmp_path / "d.shape"
    r = _cli(
        "profile", str(csv), "-o", str(out), "--sign", str(priv), env={"SHAPE_KEY_PASSPHRASE": PASS}
    )
    assert r.returncode == 0, r.stderr
    assert _cli("verify", str(out), "--key", str(pub)).returncode == 0


def test_passphrase_has_no_command_line_form():
    out = _cli("sign", "--help").stdout
    assert "--passphrase-env" in out and "--passphrase-stdin" in out
    for flag in ("--passphrase ", "--passphrase=", "--password"):
        assert flag not in out
    r = _cli("keygen", "x", "--passphrase", "abc")
    assert r.returncode == 2


def test_read_passphrase_order_and_prompt():
    env = {"A": "from-a", "SHAPE_KEY_PASSPHRASE": "from-default"}
    assert read_passphrase(env="A", environ=env) == b"from-a"
    assert (
        read_passphrase(use_stdin=True, stdin=io.StringIO("line\nrest\n"), environ=env) == b"line"
    )
    assert read_passphrase(environ=env) == b"from-default"
    assert read_passphrase(environ={}, interactive=False, prompt="p") is None
    answers = iter(["one", "one"])
    got = read_passphrase(
        environ={}, interactive=True, prompt="p", confirm=True, ask=lambda _: next(answers)
    )
    assert got == b"one"
    answers = iter(["one", "two"])
    with pytest.raises(ValueError, match="do not match"):
        read_passphrase(
            environ={}, interactive=True, prompt="p", confirm=True, ask=lambda _: next(answers)
        )
    with pytest.raises(ValueError, match="MISSING"):
        read_passphrase(env="MISSING", environ={})


# ---- the not-verified notice -----------------------------------------------------------------


def _notices(fn):
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = fn()
    return result, [str(x.message) for x in w if issubclass(x.category, ArtifactNotVerifiedWarning)]


def test_unsigned_read_notices_and_reports_status(tmp_path):
    p = _make(tmp_path)
    res, notes = _notices(lambda: read_model(p))
    assert res.signature == {"status": "unsigned", "verified": False, "key_id": None}
    assert len(notes) == 1 and "not signed" in notes[0] and str(p) in notes[0]
    m, model = res  # still unpacks as a pair
    assert m["format"] == "shape"
    assert isinstance(model, dict)


def test_signed_but_unverified_read_notices_with_key_id(tmp_path):
    sk, pk = generate_keypair()
    p = _make(tmp_path)
    sign_artifact(p, sk)
    for reader in (read_model, read_shape, read_artifact):
        res, notes = _notices(lambda reader=reader: reader(p))
        assert res.signature["status"] == "signed_not_verified"
        assert res.signature["verified"] is False
        assert res.signature["key_id"] == key_id(pk)
        assert len(notes) == 1 and key_id(pk) in notes[0] and "not verified" in notes[0]


def test_verified_read_has_no_notice(tmp_path):
    sk, pk = generate_keypair()
    p = _make(tmp_path)
    sign_artifact(p, sk)
    res, notes = _notices(lambda: read_model(p, verify_key=pk))
    assert notes == []
    assert res.signature == {"status": "verified", "verified": True, "key_id": key_id(pk)}


def test_invalid_signatures_still_fail_closed(tmp_path):
    sk, pk = generate_keypair()
    _, other = generate_keypair()
    p = _make(tmp_path)
    with pytest.raises(ArtifactSignatureError):
        read_model(p, verify_key=pk)  # unsigned
    sign_artifact(p, sk)
    with pytest.raises(ArtifactSignatureError):
        read_model(p, verify_key=other)  # unknown key
    with zipfile.ZipFile(p) as z:
        members = {i.filename: z.read(i.filename) for i in z.infolist()}
    manifest = json.loads(members["manifest.json"])
    manifest["name"] = "forged"
    members["manifest.json"] = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    with zipfile.ZipFile(p, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    with pytest.raises(ArtifactSignatureError):
        verify_artifact(p, pk)
    with pytest.raises(ArtifactSignatureError):
        read_artifact(p, verify_key=pk)


def test_signing_internals_and_notice_opt_out_are_silent(tmp_path):
    sk, _ = generate_keypair()
    p = _make(tmp_path)
    _, notes = _notices(lambda: sign_artifact(p, sk))
    assert notes == []
    _, notes = _notices(lambda: read_artifact(p, notice=False))
    assert notes == []


def test_notice_handler_can_be_replaced(tmp_path):
    got = []
    previous = set_notice_handler(got.append)
    try:
        read_model(_make(tmp_path))
    finally:
        set_notice_handler(previous)
    assert len(got) == 1 and "not signed" in got[0]


def test_malicious_key_id_is_not_echoed(tmp_path):
    p = _make(tmp_path)
    with zipfile.ZipFile(p) as z:
        members = {i.filename: z.read(i.filename) for i in z.infolist()}
    members["manifest.sig"] = json.dumps(
        {"algorithm": "Ed25519", "key_id": "\x1b]0;pwn\x07", "signature": "AAAA"}
    ).encode()
    with zipfile.ZipFile(p, "w") as z:
        for k, v in members.items():
            z.writestr(k, v)
    res, notes = _notices(lambda: read_model(p))
    assert res.signature["key_id"] is None
    assert "\x1b" not in notes[0]


def test_cli_prints_the_notice_on_stderr_once(tmp_path):
    priv, pub = write_keypair(tmp_path / "k", PASS)
    signed = _make(tmp_path, "s.shape")
    sign_artifact(signed, load_private_key(priv, PASS))
    plain = _make(tmp_path, "p.shape")
    r = _cli("inspect", str(signed))
    assert r.returncode == 0
    assert r.stderr.count("shape: note:") == 1
    assert "signed by key" in r.stderr and "not verified" in r.stderr
    assert "signed by key" not in r.stdout
    assert json.loads(r.stdout)["signature"]["status"] == "signed_not_verified"
    r = _cli("inspect", str(plain))
    assert r.returncode == 0 and "is not signed" in r.stderr
    assert json.loads(r.stdout)["signature"]["status"] == "unsigned"
    # with the trusted key there is nothing to say; a wrong key is still exit 1
    r = _cli("inspect", str(signed), "--verify", str(pub))
    assert r.returncode == 0 and "note:" not in r.stderr
    assert json.loads(r.stdout)["signature"]["status"] in ("verified", "signed_not_verified")
    _, other = write_keypair(tmp_path / "o", PASS)
    r = _cli("inspect", str(signed), "--verify", str(tmp_path / "o.pub"))
    assert r.returncode == 1 and "signature check failed" in r.stderr


# ---- spec rules still hold --------------------------------------------------------------------


def test_signing_input_is_domain_plus_exact_manifest_bytes(tmp_path):
    from shape.artifact.signing import DOMAIN
    from shape.security.crypto import verify_ed25519

    priv, pub = write_keypair(tmp_path / "k", PASS)
    p = _make(tmp_path)
    sign_artifact(p, load_private_key(priv, PASS))
    with zipfile.ZipFile(p) as z:
        manifest = z.read("manifest.json")
        sig = base64.b64decode(json.loads(z.read("manifest.sig"))["signature"])
    verify_ed25519(DOMAIN + manifest, sig, load_public_key(pub))


def test_signed_container_is_byte_reproducible_with_an_encrypted_key(tmp_path):
    priv, _ = write_keypair(tmp_path / "k", PASS)
    sk = load_private_key(priv, PASS)
    a, b = _make(tmp_path, "a.shape"), _make(tmp_path, "b.shape")
    sign_artifact(a, sk)
    sign_artifact(b, load_private_key(priv, PASS))
    assert a.read_bytes() == b.read_bytes()
    sign_artifact(a, sk)  # signing again replaces the signature with the same bytes
    assert a.read_bytes() == b.read_bytes()
