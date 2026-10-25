"""CLI Delta constraint strings survive the generation binding without storage."""

import argparse
import json

import pyarrow as pa
import pytest

from shape.builtins.sinks import delta, delta_features
from shape.cli import generation
from shape.cli.main import main
from shape.generation import output
from shape.generation.schema import Column, GenSchema, Model, Table

_MAPPING = json.dumps(
    {
        "not_null": ["id"],
        "check": {"positive": {"op": ">", "left": {"column": "id"}, "right": {"literal": 0}}},
    }
)
_CASES = [
    pytest.param("true", 3, {"not_null_id"}, id="true"),
    pytest.param("false", 2, set(), id="false"),
    pytest.param(_MAPPING, 3, {"not_null_id", "positive"}, id="json-mapping"),
]


def schema():
    return GenSchema(
        Model("preview", "preview"),
        {"t": Table("t", {"id": Column("id", "integer", {"strategy": "sequence"})})},
    )


def argv(tmp_path, raw):
    return [
        "generate",
        "preview",
        "--format",
        "delta",
        "--dry-run",
        "--json",
        "--sink-config",
        "delta.constraints=" + raw,
        "--output",
        str(tmp_path / "absent"),
    ]


def assert_protocol(protocol, writer):
    assert protocol == {
        "min_reader_version": 1,
        "min_writer_version": writer,
        "reader_features": [],
        "writer_features": ["checkConstraints"] if writer == 3 else [],
    }


@pytest.fixture(autouse=True)
def no_storage(monkeypatch):
    def refuse(*args, **kwargs):
        pytest.fail("constraint parsing must not resolve storage or load the native writer")

    monkeypatch.setattr(delta, "_location", refuse)
    monkeypatch.setattr(delta, "_deltalake", refuse)


@pytest.mark.parametrize("raw,writer,names", _CASES)
def test_cli_constraint_string_preview(tmp_path, monkeypatch, capsys, raw, writer, names):
    monkeypatch.setattr(generation, "load_target", lambda *args: schema())
    assert main(argv(tmp_path, raw)) == 0
    assert_protocol(json.loads(capsys.readouterr().out)["delta_protocol"], writer)
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("raw,writer,names", _CASES)
def test_cli_constraint_string_generation_binding(tmp_path, raw, writer, names):
    parser = argparse.ArgumentParser()
    generation.add_arguments(parser.add_subparsers())
    args = parser.parse_args(argv(tmp_path, raw))
    bound = output._options("delta", schema(), "t", generation._sink_options(args))
    assert_protocol(delta.DeltaSink.feature_plan(bound), writer)
    features = delta_features.parse(bound)
    features.schema(pa.schema([pa.field("id", pa.int64(), nullable=False)]))
    assert set(features.constraints) == names
    assert not (tmp_path / "absent").exists()
