"""SAC-01: identical content gives identical .shape bytes, in any process, signed or not."""

from __future__ import annotations

import os
import subprocess
import sys
import time
import zipfile

import pytest

from shape.artifact import read_model, read_shape, sign_artifact, verify_artifact, write_model
from shape.artifact.io import canonical_json, read_artifact, write_artifact
from shape.artifact.signing import generate_keypair

_WRITE = (
    "import sys; from shape.artifact import write_model; "
    "write_model(sys.argv[1], {'tables': {'t': {'columns': {'a': {'dtype': 'int64'}}}}}, "
    "name='t', metadata={'k': 'v'})"
)


def _write_in_process(path, hashseed: str) -> None:
    env = {**os.environ, "PYTHONHASHSEED": hashseed}
    subprocess.run([sys.executable, "-c", _WRITE, str(path)], check=True, env=env)


def test_two_processes_write_equal_bytes(tmp_path):
    a, b = tmp_path / "a.shape", tmp_path / "b.shape"
    _write_in_process(a, "1")
    time.sleep(2.1)  # a zip timestamp has 2-second resolution
    _write_in_process(b, "2")
    assert a.read_bytes() == b.read_bytes()


def test_container_fields_are_fixed(tmp_path):
    p = tmp_path / "a.shape"
    write_artifact(p, {"format": "shape"}, {"z.json": b"1", "b/a.json": b"2", "a.json": b"3"})
    with zipfile.ZipFile(p) as z:
        infos = z.infolist()
    assert [i.filename for i in infos] == ["manifest.json", "a.json", "b/a.json", "z.json"]
    for i in infos:
        assert i.date_time == (1980, 1, 1, 0, 0, 0)
        assert i.compress_type == zipfile.ZIP_STORED
        assert i.create_system == 3
        assert i.external_attr == 0o100644 << 16
        assert i.extra == b"" and i.comment == b""


def test_component_insertion_order_does_not_matter(tmp_path):
    a, b = tmp_path / "a.shape", tmp_path / "b.shape"
    write_artifact(a, {"format": "shape"}, {"x": b"1", "y": b"2"})
    write_artifact(b, {"format": "shape"}, {"y": b"2", "x": b"1"})
    assert a.read_bytes() == b.read_bytes()


@pytest.mark.sign
def test_signed_artifacts_are_reproducible_and_signature_is_container_independent(tmp_path):
    sk, pk = generate_keypair()
    a, b = tmp_path / "a.shape", tmp_path / "b.shape"
    write_model(a, {"tables": {}}, name="t")
    write_model(b, {"tables": {}}, name="t")
    time.sleep(2.1)
    sign_artifact(a, sk)
    sign_artifact(b, sk)
    assert a.read_bytes() == b.read_bytes()  # Ed25519 is deterministic, the container fixed
    verify_artifact(a, pk)
    with zipfile.ZipFile(a) as z:
        assert [i.filename for i in z.infolist()][-1] == "manifest.sig"
        manifest, sig = z.read("manifest.json"), z.read("manifest.sig")
    # The same manifest and signature in a different container (deflate, other time, order):
    other = tmp_path / "other.shape"
    _, parts = read_artifact(a)
    with zipfile.ZipFile(other, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(zipfile.ZipInfo("manifest.sig", (2031, 5, 6, 7, 8, 8)), sig)
        for k, v in parts.items():
            z.writestr(zipfile.ZipInfo(k, (2030, 1, 2, 3, 4, 6)), v)
        z.writestr(zipfile.ZipInfo("manifest.json", (2029, 1, 2, 3, 4, 6)), manifest)
    verify_artifact(other, pk)
    sign_artifact(other, sk)  # re-signing normalizes the container
    assert other.read_bytes() == a.read_bytes()


def test_legacy_deflated_artifact_still_reads(tmp_path):
    """A file written by an earlier version (deflate, real timestamps) is read as before."""
    body = b'{"a": 1}'
    import hashlib

    manifest = {"format": "x", "content_hashes": {"c.json": hashlib.sha256(body).hexdigest()}}
    p = tmp_path / "old.shape"
    with zipfile.ZipFile(p, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.json", canonical_json(manifest))
        z.writestr("c.json", body)
    m, parts = read_artifact(p)
    assert parts == {"c.json": body} and m["format"] == "x"


def test_read_model_roundtrip_after_stored_write(tmp_path):
    p = tmp_path / "m.shape"
    cid = write_model(p, {"tables": {}}, name="t")
    assert read_model(p)[0]["shape_content_id"] == cid
    assert read_shape(p)[0]["name"] == "t"
