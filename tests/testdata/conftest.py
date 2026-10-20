"""Declared test-author types for successful script seed scenarios."""

import json

import pytest


@pytest.fixture
def retail_schema(tmp_path):
    from shape.generation.domains import load_domain

    doc = load_domain("retail").schema.to_dict()
    for table in doc["tables"].values():
        for column in table["columns"].values():
            if column["type"] == "decimal":
                column["precision"] = 18
                column["scale"] = 2
    path = tmp_path / "retail-explicit-decimals.json"
    path.write_text(json.dumps(doc))
    return str(path)
