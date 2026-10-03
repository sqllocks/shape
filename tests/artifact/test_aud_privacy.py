"""Regression tests for the AUD-privacy audit (artifact package)."""

from __future__ import annotations

import os
import stat
import warnings

import pytest

from shape.artifact.keys import load_private_key, load_public_key, write_keypair
from shape.artifact.signing import generate_keypair
from shape.security.credrefs import CredentialReferenceError

posix_only = pytest.mark.skipif(os.name != "posix", reason="mode bits are POSIX only")


def _keypair(tmp_path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return write_keypair(tmp_path / "k", None, unencrypted=True, generate=generate_keypair)


# --- #402: one permission rule for a private key file -------------------------------------------


@posix_only
@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604])
def test_a_plain_path_private_key_others_can_read_is_refused(tmp_path, mode):
    priv, _ = _keypair(tmp_path)
    os.chmod(priv, mode)
    with pytest.raises(CredentialReferenceError, match="chmod 600"):
        load_private_key(str(priv))


@posix_only
def test_a_plain_path_private_key_with_mode_0600_is_read(tmp_path):
    priv, _ = _keypair(tmp_path)
    assert stat.S_IMODE(os.stat(priv).st_mode) == 0o600
    assert len(load_private_key(str(priv))) == 32


@posix_only
def test_a_world_readable_public_key_is_still_read(tmp_path):
    _, pub = _keypair(tmp_path)
    os.chmod(pub, 0o644)
    assert len(load_public_key(str(pub))) == 32
