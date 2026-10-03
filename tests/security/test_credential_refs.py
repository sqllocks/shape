"""P6-07b: the shared credential-reference resolver (``env://``, ``file://``, ``kv://``) and the
redaction of secrets in text. The cloud-specific parts (Key Vault, the sign-in modes) are tested
with the Fabric plugin; here core is checked on its own, with no cloud SDK."""

from __future__ import annotations

import os
import stat
import sys
import types

import pytest

from shape.security import credrefs
from shape.security.redact import MASK, holds_secret, redact_text

pytestmark = pytest.mark.security

SECRET = "Zx9-very-secret-value-4417"
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


@pytest.fixture
def no_providers(monkeypatch):
    """No installed package provides a resolver (the Fabric plugin provides ``kv``)."""
    monkeypatch.setattr(credrefs, "_PROVIDERS", {})
    monkeypatch.setattr(credrefs, "_LOADED", {})


# --- file:// permissions -----------------------------------------------------------------


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o660, 0o666, 0o750, 0o601])
def test_file_reference_refuses_a_file_others_can_access(tmp_path, mode):
    f = tmp_path / "s.txt"
    f.write_text(SECRET)
    f.chmod(mode)
    with pytest.raises(credrefs.CredentialReferenceError) as err:
        credrefs.resolve_reference(f"file://{f}")
    text = str(err.value)
    assert "chmod 600" in text and f"{stat.S_IMODE(mode):04o}" in text
    assert SECRET not in text
    assert str(f) in text


@posix_only
@pytest.mark.parametrize("mode", [0o600, 0o400, 0o700])
def test_file_reference_accepts_a_private_file(tmp_path, mode):
    f = tmp_path / "s.txt"
    f.write_text(SECRET + "\n")
    f.chmod(mode)
    assert credrefs.resolve_reference(f"file://{f}") == SECRET


@posix_only
def test_a_file_that_is_not_secret_can_be_read_with_private_false(tmp_path):
    f = tmp_path / "pub.txt"
    f.write_text("public-material")
    f.chmod(0o644)
    assert credrefs.resolve_reference(f"file://{f}", private=False) == "public-material"


@posix_only
def test_the_refusal_comes_before_the_read(tmp_path, monkeypatch):
    f = tmp_path / "s.txt"
    f.write_text(SECRET)
    f.chmod(0o644)
    reads = []
    real = type(f).read_text
    monkeypatch.setattr(
        type(f), "read_text", lambda self, *a, **k: reads.append(self) or real(self)
    )
    with pytest.raises(credrefs.CredentialReferenceError):
        credrefs.resolve_reference(f"file://{f}")
    assert reads == []  # the secret was never read into memory


# --- kv:// through an installed package --------------------------------------------------


def _provided_by(monkeypatch, module):
    monkeypatch.setattr(credrefs, "_PROVIDERS", {"kv": ("shape_test_provider", "resolve")})
    monkeypatch.setattr(credrefs, "_LOADED", {})
    monkeypatch.setitem(sys.modules, "shape_test_provider", module)


def test_kv_is_taken_from_the_package_that_provides_it(monkeypatch):
    calls = []
    module = types.ModuleType("shape_test_provider")
    module.resolve = lambda rest: calls.append(rest) or "from-the-plugin"
    _provided_by(monkeypatch, module)
    assert credrefs.is_reference("kv://vault/name")
    assert credrefs.resolve_reference("kv://vault/name") == "from-the-plugin"
    assert calls == ["vault/name"]


def test_an_explicit_registration_beats_the_installed_package(monkeypatch):
    module = types.ModuleType("shape_test_provider")
    module.resolve = lambda rest: "from-the-plugin"
    _provided_by(monkeypatch, module)
    credrefs.register_resolver("kv", lambda rest: "registered")
    try:
        assert credrefs.resolve_reference("kv://v/n") == "registered"
    finally:
        credrefs.unregister_resolver("kv")
    assert credrefs.resolve_reference("kv://v/n") == "from-the-plugin"


def test_a_package_that_cannot_be_imported_is_reported_as_no_resolver(monkeypatch):
    _provided_by(monkeypatch, None)  # a None entry in sys.modules makes the import fail
    with pytest.raises(credrefs.CredentialReferenceError) as err:
        credrefs.resolve_reference("kv://v/n")
    assert "kv://" in str(err.value) and "resolver" in str(err.value)


def test_without_a_package_kv_says_what_to_install(no_providers):
    with pytest.raises(credrefs.CredentialReferenceError, match=r"sqllocks-shape\[fabric\]"):
        credrefs.resolve_reference("kv://v/n")


def test_core_imports_no_cloud_sdk_to_resolve_references(tmp_path, monkeypatch, no_providers):
    f = tmp_path / "s"
    f.write_text("x")
    f.chmod(0o600)
    monkeypatch.setenv("SHAPE_T_ENV", "y")
    credrefs.resolve_reference(f"file://{f}")
    credrefs.resolve_reference("env://SHAPE_T_ENV")
    assert not [m for m in sys.modules if m.startswith(("azure", "boto", "google.cloud"))]


def test_recognising_a_reference_imports_nothing():
    before = set(sys.modules)
    assert credrefs.is_reference("https://example.test/x") is False
    assert credrefs.is_reference("env://X") is True
    assert credrefs.is_reference("kv://v/n") is True
    assert not [m for m in set(sys.modules) - before if m.startswith(("shape_fabric", "azure"))]


# --- redaction ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, hidden",
    [
        ("Server=db;PWD=hunter2;Encrypt=yes", "hunter2"),
        ("Server=db;Password={a;b}}c};Encrypt=yes", "a;b"),
        ("Server=db;pwd='quoted value';x=y", "quoted value"),
        ("login failed for Pwd=hunter2 on db", "hunter2"),
        ("DefaultEndpointsProtocol=https;AccountKey=abc123abc123abc;X=1", "abc123abc123abc"),
        ("Endpoint=sb://x/;SharedAccessKey=k3y-value", "k3y-value"),
        ("url?sig=signature-value&se=2030", "signature-value"),
        ("client_secret=very-secret-1 tenant=x", "very-secret-1"),
        ("Authorization: Bearer abcdefghijkl.mnopqrstuv", "abcdefghijkl"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.sig-part", "eyJhbGciOiJIUzI1NiJ9"),
        ("https://user:p4ss@host.example/path", "p4ss"),
        ("-----BEGIN PRIVATE KEY-----\nMIIEvQ\n-----END PRIVATE KEY-----", "MIIEvQ"),
        ("accesstoken=tok123tok123", "tok123tok123"),
    ],
)
def test_redact_text_hides_the_secret_and_keeps_the_rest(text, hidden):
    out = redact_text(text)
    assert hidden not in out and MASK in out


def test_redact_text_leaves_ordinary_text_alone():
    text = "could not connect to Server=db.example.test;Database=d: timed out after 30s"
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Server=x;PWD=y", True),
        ("Server=x;password=y", True),
        ("AccountKey=abc", True),
        ("SharedAccessKey=abc", True),
        ("Server=x;Database=y;Encrypt=yes", False),
        ("env://VARIABLE", False),
    ],
)
def test_holds_secret(text, expected):
    assert holds_secret(text) is expected


# --- no secret reaches an error from a resolver ------------------------------------------


def test_a_resolver_that_fails_with_the_secret_in_its_message_is_not_echoed():
    def leaky(rest):
        raise RuntimeError(f"vault said no: {SECRET}")

    credrefs.register_resolver("kv", leaky)
    try:
        with pytest.raises(credrefs.CredentialReferenceError) as err:
            credrefs.resolve_reference("kv://v/n")
    finally:
        credrefs.unregister_resolver("kv")
    assert SECRET not in str(err.value) and SECRET not in repr(err.value)
    assert err.value.__cause__ is None


def test_env_and_file_errors_name_the_reference_never_a_value(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAPE_T_EMPTY", "")
    with pytest.raises(credrefs.CredentialReferenceError, match="SHAPE_T_EMPTY is empty"):
        credrefs.resolve_reference("env://SHAPE_T_EMPTY")
    f = tmp_path / "empty"
    f.write_text("\n")
    f.chmod(0o600)
    with pytest.raises(credrefs.CredentialReferenceError, match="is empty"):
        credrefs.resolve_reference(f"file://{f}")


def test_the_fake_cloud_module_is_not_needed(monkeypatch):
    # sanity: resolution works with a stub package named like the SDK absent from sys.modules
    monkeypatch.setitem(sys.modules, "azure", types.ModuleType("azure"))
    monkeypatch.setenv("SHAPE_T_X", "v")
    assert credrefs.resolve_reference("env://SHAPE_T_X") == "v"
