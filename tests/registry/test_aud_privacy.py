"""Regression tests for the AUD-privacy audit (registries)."""

from __future__ import annotations

import io
import json
import zipfile

import pytest

from shape.registry.local import LocalRegistry, RawProfileError, is_raw_profile


def _zip(members: dict[str, bytes], compression: int = zipfile.ZIP_STORED) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=compression) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return buf.getvalue()


# --- #396 / #283: the raw-profile sniff --------------------------------------------------------


@pytest.mark.parametrize(
    "manifest",
    [b"[]", b"5", b'"x"', b"[" * 100_000, b"{bad"],
    ids=["list", "number", "string", "deep", "invalid"],
)
def test_a_zip_with_an_odd_manifest_is_not_raw_and_does_not_crash(manifest):
    assert is_raw_profile(_zip({"manifest.json": manifest})) is False


@pytest.mark.parametrize(
    "data",
    [b"{" + b"[" * 100_000, b"[" * 100_000, b"\x00\xff\xfe"],
    ids=["deep-object", "deep-array", "binary"],
)
def test_odd_bytes_are_not_raw_and_do_not_crash(data):
    assert is_raw_profile(data) is False


@pytest.mark.parametrize(
    "encode",
    [
        lambda s: b"\xef\xbb\xbf" + s.encode("utf-8"),
        lambda s: s.encode("utf-16"),
        lambda s: s.encode("utf-16-le"),
        lambda s: s.encode("utf-32"),
        lambda s: b"\n\t " + s.encode("utf-8"),
    ],
    ids=["utf-8-bom", "utf-16", "utf-16-le", "utf-32", "leading-space"],
)
def test_an_exported_raw_profile_is_raw_however_it_is_encoded(encode):
    doc = json.dumps({"format": "shape-profile", "tables": {}})
    assert is_raw_profile(encode(doc)) is True


def test_a_raw_profile_with_a_bom_is_refused_by_commit(tmp_path):
    data = b"\xef\xbb\xbf" + json.dumps({"format": "shape-profile"}).encode()
    with pytest.raises(RawProfileError):
        LocalRegistry(tmp_path).commit("n", data)


def test_a_zip_profile_with_leading_bytes_is_raw():
    data = b"junk" + _zip({"manifest.json": json.dumps({"kind": "profile"}).encode()})
    assert is_raw_profile(data) is True


def test_the_manifest_sniff_does_not_inflate_a_bomb():
    bomb = _zip(
        {"manifest.json": b" " * (64 * 1024 * 1024) + b'{"kind":"profile"}'},
        zipfile.ZIP_DEFLATED,
    )
    assert len(bomb) < 1024 * 1024
    import tracemalloc

    tracemalloc.start()
    try:
        result = is_raw_profile(bomb)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert result is False
    assert peak < 16 * 1024 * 1024
