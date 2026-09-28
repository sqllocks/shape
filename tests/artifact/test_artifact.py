from shape.artifact import read_artifact, write_artifact


def test_roundtrip(tmp_path):
    p = tmp_path / "x.shape"
    write_artifact(p, {"artifact_format_version": "1"}, {"evidence/a.json": b"{}"})
    m, c = read_artifact(p)
    assert c["evidence/a.json"] == b"{}"
