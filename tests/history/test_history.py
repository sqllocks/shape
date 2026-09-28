from shape.history import LocalHistory


def test_content_addressed_history(tmp_path):
    h = LocalHistory(tmp_path)
    e = h.append(b"abc")
    assert h.checkout(e.content_hash) == b"abc" and len(h.entries()) == 1
