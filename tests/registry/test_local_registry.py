import os
import sys

import pytest

from shape.registry import LocalRegistry, RegistryError


def test_registry_commit_tag_promote_checkout(tmp_path):
    r = LocalRegistry(tmp_path)
    a = r.commit("customer", b"one")
    b = r.commit("customer", b"two")
    assert a != b and r.checkout("customer") == b"two"
    assert r.tag("customer", "v1", a) == a and r.checkout("customer", "v1") == b"one"
    r.promote("customer", "v1", "production")
    assert r.checkout("customer", "production") == b"one"
    assert len(r.log("customer")) == 2


@pytest.mark.security
@pytest.mark.parametrize(
    "name", ["../x", "/abs/x", "a/b", "..", ".hidden", "", "a\\b", "x" * 129, "a\x00b"]
)
def test_bad_names_rejected(tmp_path, name):  # SEC5
    r = LocalRegistry(tmp_path / "reg")
    with pytest.raises(RegistryError):
        r.commit(name, b"x")
    with pytest.raises(RegistryError):
        r.resolve(name)
    assert not (tmp_path / "x").exists() and not (tmp_path / "x.jsonl").exists()


@pytest.mark.security
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privileges on Windows")
def test_symlink_escape_rejected(tmp_path):  # SEC5
    root = tmp_path / "reg"
    r = LocalRegistry(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, root / "refs" / "evil")
    with pytest.raises(RegistryError):
        r.commit("evil", b"x")
    assert list(outside.iterdir()) == []


@pytest.mark.security
def test_checkout_other_names_object_rejected(tmp_path):  # SEC4
    import hashlib

    r = LocalRegistry(tmp_path)
    secret = hashlib.sha256(b"secret").hexdigest()
    r.commit("private", b"secret")
    r.commit("public", b"hello")
    with pytest.raises(RegistryError):
        r.checkout("public", secret)
    assert r.checkout("private", secret) == b"secret"
