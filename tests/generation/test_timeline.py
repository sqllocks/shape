from shape.generation import ShapeTimeline, VersionedShape


def S(mean):
    return {
        "rows": 10,
        "columns": {
            "x": {
                "kind": "numeric",
                "count": 10,
                "mean": mean,
                "variance_population": 1,
                "min": mean - 3,
                "max": mean + 3,
            }
        },
    }


def test_timeline_interpolate_and_generate_range():
    t = ShapeTimeline(
        [
            VersionedShape("v1", 0, S(0)),
            VersionedShape("v2", 10, S(10)),
            VersionedShape("v3", 20, S(20)),
        ]
    )
    assert abs(t.shape_at(5)["columns"]["x"]["mean"] - 5) < 1e-9
    out = t.generate_range(0, 20, 5, 1000, 4)
    assert len(out) == 5 and all(len(x[1]["x"]) == 1000 for x in out)
