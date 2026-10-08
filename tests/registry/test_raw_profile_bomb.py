"""AUD-security2 #283: the raw-profile check reads a container's manifest within the artifact
manifest limit, never inflating a hostile member into memory."""

from __future__ import annotations

import io
import tracemalloc
import zipfile

import pytest

from shape.registry.local import RegistryError, is_raw_profile


def _container(manifest: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", manifest)
    return buf.getvalue()


def test_a_manifest_bomb_is_refused_without_inflating_it():
    data = _container(b" " * (64 << 20) + b'{"kind": "profile"}')
    assert len(data) < 1 << 20
    tracemalloc.start()
    try:
        with pytest.raises(RegistryError, match="manifest"):
            is_raw_profile(data)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 16 << 20


def test_a_raw_profile_container_is_still_recognised():
    assert is_raw_profile(_container(b'{"kind": "profile"}'))
    assert not is_raw_profile(_container(b'{"kind": "model"}'))
