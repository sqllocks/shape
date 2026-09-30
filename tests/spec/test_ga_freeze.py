import json
from pathlib import Path

from shape.artifact import read_shape, write_shape
from shape.contracts import compatibility

ROOT = Path(__file__).resolve().parents[2]


def test_ga_schema_is_version_one_and_closed():
    s = json.loads((ROOT / "src/shape/schemas/shape-v1-ga.schema.json").read_text())
    assert s["properties"]["version"]["const"] == 1 and s["additionalProperties"] is False
    assert "/1.0/" in s["$id"]


def test_v1_artifact_roundtrip_is_stable(tmp_path):
    shape = {
        "rows": 2,
        "columns": {
            "id": {
                "kind": "numeric",
                "count": 2,
                "null_count": 0,
                "mean": 1.5,
                "variance_population": 0.25,
                "min": 1,
                "max": 2,
            }
        },
    }
    p = tmp_path / "x.shape"
    cid = write_shape(p, shape, name="x")
    m, out = read_shape(p)
    assert out == shape and m["format_version"] == 1 and m["shape_content_id"] == cid


def test_additive_column_compatibility_is_explicit():
    before = {"columns": {"id": {"kind": "numeric"}}}
    after = {"columns": {"id": {"kind": "numeric"}, "new": {"kind": "text"}}}
    # The API must return a governed result rather than infer compatibility silently.
    r = compatibility(before, after, "backward")
    assert isinstance(r.compatible, bool) and r.mode == "backward"
