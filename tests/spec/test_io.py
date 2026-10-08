import json

from shape.spec import load_contract


def test_json_contract(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(
        json.dumps({"name": "x", "version": 1, "fidelity": "gold", "fields": [], "metadata": {}})
    )
    assert load_contract(p).name == "x"
