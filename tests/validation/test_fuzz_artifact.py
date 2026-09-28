from zipfile import BadZipFile

import pytest

from shape.artifact.io import ArtifactError, read_artifact
from shape.validation.fuzz import artifact_cases


def test_malformed_artifacts_rejected(tmp_path):
    for name, data in artifact_cases().items():
        p = tmp_path / (name + ".shape")
        p.write_bytes(data)
        with pytest.raises((ArtifactError, BadZipFile)):
            read_artifact(p)
