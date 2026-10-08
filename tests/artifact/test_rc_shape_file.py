import zipfile

import pytest

from shape.artifact import ArtifactError, read_shape, write_shape


def test_shape_roundtrip_and_identity(tmp_path):
    p = tmp_path / "x.shape"
    cid = write_shape(p, {"rows": 2, "columns": {"x": {"kind": "numeric", "count": 2}}}, name="x")
    m, s = read_shape(p)
    assert m["shape_content_id"] == cid and s["rows"] == 2


def test_shape_corruption_rejected(tmp_path):
    p = tmp_path / "x.shape"
    write_shape(p, {"rows": 1, "columns": {}})
    q = tmp_path / "bad.shape"
    with zipfile.ZipFile(p) as zin, zipfile.ZipFile(q, "w") as zout:
        for i in zin.infolist():
            b = zin.read(i.filename)
            if i.filename == "shape.json":
                b = b.replace(b'"rows":1', b'"rows":2')
            zout.writestr(i.filename, b)
    with pytest.raises(ArtifactError):
        read_shape(q)
