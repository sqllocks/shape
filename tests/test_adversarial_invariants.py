import random

from shape.artifact import read_shape, write_shape
from shape.capture import capture_rows
from shape.generation import generate_from_shape


def test_500_random_capture_artifact_generation_invariants(tmp_path):
    rng = random.Random(9)
    for case in range(500):
        n = rng.randint(1, 150)
        rows = []
        for i in range(n):
            rows.append(
                {
                    "id": i,
                    "x": None if rng.random() < 0.15 else rng.randint(-1000, 1000),
                    "cat": rng.choice(["a", "b", "c", None]),
                }
            )
        s = capture_rows(rows).to_dict()
        p = tmp_path / f"{case}.shape"
        write_shape(p, s)
        _, loaded = read_shape(p)
        assert loaded["rows"] == n
        data, report = generate_from_shape(loaded, n, case)
        assert report.rows == n
        assert all(len(v) == n for v in data.values())
