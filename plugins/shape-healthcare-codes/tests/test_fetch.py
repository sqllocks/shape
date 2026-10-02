import hashlib
from pathlib import Path

import pytest
from shape_healthcare_codes import fetch


@pytest.fixture(autouse=True)
def _allow_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fetch, "ALLOWED_SCHEMES", ("https", "file"))


def test_download_verifies_the_pin_and_caches(tmp_path: Path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"release bytes")
    pin = "sha256:" + hashlib.sha256(b"release bytes").hexdigest()
    dest = tmp_path / "dl"
    got = fetch.download(src.as_uri(), dest, pin=pin)
    assert got.read_bytes() == b"release bytes"
    src.unlink()  # the source is gone: the cached copy that passes the pin is reused
    assert fetch.download(src.as_uri(), dest, pin=pin) == got


def test_a_changed_file_fails_the_pin_and_is_not_kept(tmp_path: Path):
    src = tmp_path / "src.bin"
    src.write_bytes(b"changed")
    with pytest.raises(fetch.ChecksumMismatch, match="pinned"):
        fetch.download(src.as_uri(), tmp_path / "dl", pin="sha256:" + "0" * 64)
    assert not list((tmp_path / "dl").glob("src.bin"))


def test_md5_pins_and_digest_names(tmp_path: Path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    assert (
        fetch.verify(p, "md5:900150983cd24fb0d6963f7d28e17f72")
        == hashlib.sha256(b"abc").hexdigest()
    )
    assert fetch.sources_manifest({"f": p}) == {"f": hashlib.sha256(b"abc").hexdigest()}


def test_unreachable_source_is_an_error_after_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(fetch.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="could not download"):
        fetch.download((tmp_path / "missing").as_uri(), tmp_path / "dl")


def test_only_https_is_allowed_by_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(fetch, "ALLOWED_SCHEMES", ("https",))
    for url in ("file:///etc/passwd", "http://example.org/x.zip", "ftp://example.org/x"):
        with pytest.raises(ValueError, match="only https"):
            fetch.download(url, tmp_path)


def test_cache_names_do_not_collide_when_two_sources_end_alike(tmp_path: Path):
    a = tmp_path / "cm" / "updated-01" / "11" / "2023.zip"
    b = tmp_path / "pcs" / "updated-01" / "11" / "2023.zip"
    for p, data in ((a, b"cm"), (b, b"pcs")):
        p.parent.mkdir(parents=True)
        p.write_bytes(data)
    dest = tmp_path / "dl"
    assert fetch.download(a.as_uri(), dest).read_bytes() == b"cm"
    assert fetch.download(b.as_uri(), dest).read_bytes() == b"pcs"
    assert len(list(dest.iterdir())) == 2
