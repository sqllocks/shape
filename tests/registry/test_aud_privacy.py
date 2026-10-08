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

    # Lead decision E1 (2026-10-05): the sniff refuses a container whose manifest no Shape reader
    # accepts (#283) instead of answering False; either way the bomb is never inflated.
    tracemalloc.start()
    try:
        with pytest.raises(RegistryError, match=r"^not a Shape container: manifest too large$"):
            is_raw_profile(bomb)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert peak < 16 * 1024 * 1024


# --- #409: damaged registry state is a RegistryError that names it ------------------------------

from shape.registry.local import RegistryError  # noqa: E402


def test_a_torn_log_line_is_a_registry_error(tmp_path):
    r = LocalRegistry(tmp_path)
    r.commit("n", b"hello")
    with (tmp_path / "logs" / "n.jsonl").open("a") as fh:
        fh.write("{truncated\n")
    for call in (lambda: r.log("n"), lambda: r.entry("n"), lambda: r.resolve("n", "nope")):
        with pytest.raises(RegistryError, match=r"n\.jsonl.*line 2"):
            call()


def test_a_missing_object_is_a_registry_error(tmp_path):
    r = LocalRegistry(tmp_path)
    h = r.commit("n", b"hello")
    (tmp_path / "objects" / h).unlink()
    with pytest.raises(RegistryError, match="missing"):
        r.checkout("n")


def test_a_ref_that_is_not_a_content_id_is_a_registry_error(tmp_path):
    r = LocalRegistry(tmp_path)
    r.commit("n", b"hello")
    (tmp_path / "refs" / "n" / "latest").write_text("not-a-hash")
    with pytest.raises(RegistryError, match="corrupt"):
        r.checkout("n")


def test_leftover_temp_files_are_not_refs(tmp_path):
    r = LocalRegistry(tmp_path)
    r.commit("n", b"hello")
    (tmp_path / "refs" / "n" / ".tmp-abc").write_text("x")
    assert set(r.refs("n")) == {"latest"}


# --- #420: a damaged profile-registry index asks for reindex ------------------------------------


@pytest.mark.parametrize(
    "index",
    ['{"x": {"foo": 1}}', '{"x": 5}', '{"x": {"system": "s", "table": "t"}}'],
    ids=["no-keys", "not-object", "no-name"],
)
def test_a_damaged_index_entry_asks_for_reindex(tmp_path, index):
    from shape.registry.profiles import ProfileRegistry, ProfileRegistryError

    r = ProfileRegistry(tmp_path)
    (tmp_path / "_index.json").write_text(index)
    with pytest.raises(ProfileRegistryError, match="reindex"):
        r.entries(system="s")
