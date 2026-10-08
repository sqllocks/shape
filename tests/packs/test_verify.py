import hashlib

from shape.packs import ReferenceAsset, verify_asset


def test_verify(tmp_path):
    p = tmp_path / "a"
    p.write_bytes(b"abc")
    h = hashlib.sha256(b"abc").hexdigest()
    a = ReferenceAsset("a", "1", "local", "test", h)
    assert verify_asset(a, p)
    p.write_bytes(b"x")
    assert not verify_asset(a, p)
