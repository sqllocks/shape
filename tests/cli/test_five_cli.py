import json

from shape.artifact import write_shape
from shape.cli.main import main


def test_query_check_compatibility_plan_registry(tmp_path, capsys):
    s = {
        "rows": 10,
        "columns": {
            "x": {
                "kind": "numeric",
                "null_count": 0,
                "distinct_estimate": 10,
                "mean": 5,
                "variance_population": 2,
                "min": 1,
                "max": 9,
            }
        },
    }
    p = tmp_path / "x.shape"
    write_shape(p, s)
    assert main(["query", str(p), 'column("x").mean']) == 0
    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"columns": {"x": {"kind": "numeric", "unique": True}}}))
    assert main(["check", str(p), str(contract)]) == 0
    assert main(["compatibility", str(p), str(p), "--mode", "full"]) == 0
    assert main(["plan", str(p)]) == 0
    root = tmp_path / "reg"
    assert main(["registry", str(root), "commit", "x", str(p)]) == 0
    assert main(["registry", str(root), "tag", "x", "v1"]) == 0
    assert main(["registry", str(root), "promote", "x", "v1", "production"]) == 0
