"""``shape.scale.api``: the request checks and the local run's sink hand-back."""

from __future__ import annotations

import json

import pytest
from scale_schemas import plain_doc

from shape.scale import api


@pytest.fixture
def schema_file(tmp_path):
    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc({"customer": 4, "order": 10, "order_line": 25})))
    return str(path)


def test_unknown_request_keys_are_refused(schema_file):
    with pytest.raises(ValueError, match="unknown scale_generate setting.*colour"):
        api.normalize({"domain": schema_file, "colour": "blue"})


def test_chunk_size_must_be_positive(schema_file):
    with pytest.raises(ValueError, match="chunk_size must be at least 1"):
        api.normalize({"domain": schema_file, "chunk_size": 0})


def test_target_is_the_domain_when_no_domain_is_given(schema_file):
    assert api.normalize({"target": schema_file})["domain"] == schema_file


def test_run_local_refuses_a_remote_mode(schema_file):
    with pytest.raises(ValueError, match="'fabric_spark' is not a local mode"):
        api.run_local({**api.normalize({"domain": schema_file}), "scale_mode": "fabric_spark"})


def test_run_local_hands_back_its_sinks(schema_file):
    kept: list = []
    result = api.run_local(api.normalize({"domain": schema_file, "seed": 2}), keep_sinks=kept)
    assert result["rows_generated"] == 39 and result["sinks_written"] == {"memory": "ok"}
    tables = kept[0].result()
    assert {name: t.num_rows for name, t in tables.items()} == {
        "customer": 4,
        "order": 10,
        "order_line": 25,
    }


def test_the_fabric_token_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("SHAPE_FABRIC_TOKEN", "t0k")
    assert api._env_token() == "t0k"
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN")
    assert api._env_token() == ""
