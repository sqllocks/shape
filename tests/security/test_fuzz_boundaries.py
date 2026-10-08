import random
import string
import zipfile

from shape.artifact.io import ArtifactError, read_artifact
from shape.query import ShapeQueryError, query
from shape.security import validate_structure


def test_5000_random_query_inputs_never_execute_or_crash():
    rng = random.Random(42)
    s = {"rows": 1, "columns": {"x": {"kind": "numeric", "mean": 1}}}
    alphabet = string.ascii_letters + string.digits + '_"().;/\\\\[]{} '
    for _ in range(5000):
        q = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 100)))
        try:
            query(s, q)
        except ShapeQueryError:
            pass


def test_2000_random_nested_structures_bounded():
    rng = random.Random(9)
    for _ in range(2000):
        x = {"a": [rng.randint(-100, 100), None, True, "x" * rng.randint(0, 100)]}
        assert validate_structure(x) is x


def test_500_malformed_artifacts_rejected(tmp_path):
    rng = random.Random(1)
    for i in range(500):
        p = tmp_path / f"{i}.shape"
        with zipfile.ZipFile(p, "w") as z:
            if rng.random() < 0.8:
                z.writestr(
                    "manifest.json", bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 200)))
                )
            if rng.random() < 0.5:
                z.writestr("../x", b"x")
            if rng.random() < 0.5:
                z.writestr(
                    "shape.json", bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 200)))
                )
        try:
            read_artifact(p, max_member_bytes=1000, max_total_bytes=2000, max_ratio=20)
        except (ArtifactError, zipfile.BadZipFile, KeyError, UnicodeDecodeError):
            pass
