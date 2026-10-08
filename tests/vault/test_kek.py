"""Item 2: the key-encryption key."""

from __future__ import annotations

import base64
import os
import stat

import pytest

from shape.security import credrefs
from shape.vault.errors import VaultInputError
from shape.vault.kek import decode_kek, kek_id, resolve_kek, write_kek


def test_keygen_writes_32_random_bytes_base64_mode_0600(tmp_path):
    path = tmp_path / "kek.key"
    key_id = write_kek(path)
    raw = base64.b64decode(path.read_text().strip(), validate=True)
    assert len(raw) == 32
    assert key_id == kek_id(raw)
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_keygen_never_overwrites(tmp_path):
    path = tmp_path / "kek.key"
    path.write_text("existing")
    with pytest.raises(VaultInputError):
        write_kek(path)
    assert path.read_text() == "existing"


def test_keygen_keys_differ(tmp_path):
    write_kek(tmp_path / "a")
    write_kek(tmp_path / "b")
    assert (tmp_path / "a").read_text() != (tmp_path / "b").read_text()


def test_resolve_file_path_plain_and_uri(kek_file, kek):
    assert resolve_kek(str(kek_file)) == kek
    assert resolve_kek(f"file://{kek_file}") == kek


def test_resolve_env(kek):
    assert resolve_kek("env://V_KEK", environ={"V_KEK": base64.b64encode(kek).decode()}) == kek


def test_env_not_set_is_input_error():
    with pytest.raises(VaultInputError, match="V_NOPE"):
        resolve_kek("env://V_NOPE", environ={})


@pytest.mark.skipif(os.name != "posix", reason="mode bits")
@pytest.mark.parametrize("mode", [0o640, 0o604, 0o644, 0o660])
def test_group_or_world_readable_kek_is_refused(kek_file, mode):
    kek_file.chmod(mode)
    with pytest.raises(VaultInputError, match="accessible to other users"):
        resolve_kek(str(kek_file))


@pytest.mark.parametrize("n", [0, 16, 31, 33, 64])
def test_kek_that_is_not_32_bytes_is_input_error(tmp_path, n):
    path = tmp_path / "k"
    text = base64.b64encode(bytes(n)).decode()
    path.write_text(text + "\n")
    if os.name == "posix":
        path.chmod(0o600)
    with pytest.raises(VaultInputError) as e:
        resolve_kek(str(path))
    assert text not in str(e.value) or n == 0


def test_not_base64_is_input_error_without_echo():
    with pytest.raises(VaultInputError) as e:
        decode_kek("this is !! not base64 secret-ish text")
    assert "secret-ish" not in str(e.value)


def test_a_literal_key_on_the_command_line_is_refused_without_echo(kek):
    literal = base64.b64encode(kek).decode()
    with pytest.raises(VaultInputError) as e:
        resolve_kek(literal)
    assert literal not in str(e.value)


def test_unknown_scheme_is_refused():
    with pytest.raises(VaultInputError):
        resolve_kek("ftp://example/key")


def test_kv_uses_the_registered_resolver(kek):
    credrefs.register_resolver("kv", lambda rest: base64.b64encode(kek).decode())
    try:
        assert resolve_kek("kv://vault/name") == kek
    finally:
        credrefs.unregister_resolver("kv")


def test_empty_reference_is_input_error():
    with pytest.raises(VaultInputError):
        resolve_kek("")
