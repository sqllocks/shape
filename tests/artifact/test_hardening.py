import zipfile

import pytest

from shape.artifact.io import ArtifactError, read_artifact


def test_duplicate_member_rejected(tmp_path):
    p = tmp_path / "x.shape"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("manifest.json", '{"content_hashes":{}}')
        z.writestr("manifest.json", '{"content_hashes":{}}')
    with pytest.raises(ArtifactError):
        read_artifact(p)


def test_unexpected_member_rejected(tmp_path):
    p = tmp_path / "x.shape"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("manifest.json", '{"content_hashes":{}}')
        z.writestr("evil", b"x")
    with pytest.raises(ArtifactError):
        read_artifact(p)
