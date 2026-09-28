from shape.registry import LocalRegistry


def test_registry_commit_tag_promote_checkout(tmp_path):
    r = LocalRegistry(tmp_path)
    a = r.commit("customer", b"one")
    b = r.commit("customer", b"two")
    assert a != b and r.checkout("customer") == b"two"
    assert r.tag("customer", "v1", a) == a and r.checkout("customer", "v1") == b"one"
    r.promote("customer", "v1", "production")
    assert r.checkout("customer", "production") == b"one"
    assert len(r.log("customer")) == 2
