"""Delta protocol previews include checks derived by the W9-04 dependency."""

import argparse
import json

import pytest

from shape.builtins.sinks import delta
from shape.cli import generation
from shape.cli.main import main
from shape.generation.schema import BusinessRule, Column, GenSchema, Model, Table


def schema(*, business_rule):
    return GenSchema(
        Model("preview", "preview"),
        {"t": Table("t", {"id": Column("id", "integer", {"strategy": "sequence"})})},
        business_rules=[BusinessRule("positive", "constraint", "id > 0", "t")]
        if business_rule
        else [],
    )


def arguments(tmp_path, feature):
    parser = argparse.ArgumentParser()
    generation.add_arguments(parser.add_subparsers())
    argv = [
        "generate",
        "preview",
        "--format",
        "delta",
        "--dry-run",
        "--json",
        "--output",
        str(tmp_path / "absent"),
    ]
    if feature:
        argv += ["--sink-config", "delta." + feature]
    return parser.parse_args(argv)


@pytest.mark.parametrize("source", ["schema", "contract"])
@pytest.mark.parametrize(
    "feature,versions,extra",
    [
        (None, (1, 3), set()),
        ("column_mapping=name", (2, 5), {"columnMapping"}),
        ("deletion_vectors=true", (3, 7), {"deletionVectors"}),
    ],
)
def test_derived_checks_preview_protocol_union_without_storage(
    tmp_path, monkeypatch, capsys, source, feature, versions, extra
):
    """Isolate protocol reporting from the dependency's not-yet-merged CLI parser."""
    args = arguments(tmp_path, feature)
    args.sql_constraints = "all"
    args.contract = None
    if source == "contract":
        contract = tmp_path / "contract.json"
        contract.write_text('{"columns":{"id":{"min":1}}}')
        args.contract = str(contract)

    def no_storage(*args, **kwargs):
        pytest.fail("dry-run must not resolve storage or load the native Delta writer")

    monkeypatch.setattr(delta, "_location", no_storage)
    monkeypatch.setattr(delta, "_deltalake", no_storage)
    assert (
        generation._generate_schema(args, lambda: schema(business_rule=source == "schema"), {}) == 0
    )
    doc = json.loads(capsys.readouterr().out)
    protocol = doc["delta_protocol"]
    assert (protocol["min_reader_version"], protocol["min_writer_version"]) == versions
    assert set(protocol["writer_features"]) == {"checkConstraints"} | extra
    assert set(protocol["reader_features"]) == extra
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("source", ["schema", "contract"])
def test_cli_all_policy_and_contract_report_derived_delta_checks(
    tmp_path, monkeypatch, capsys, source
):
    monkeypatch.setattr(
        generation, "load_target", lambda *args: schema(business_rule=source == "schema")
    )
    argv = [
        "generate",
        "preview",
        "--format",
        "delta",
        "--dry-run",
        "--json",
        "--sql-constraints",
        "all",
        "--output",
        str(tmp_path / "absent"),
    ]
    if source == "contract":
        contract = tmp_path / "contract.json"
        contract.write_text('{"columns":{"id":{"min":1}}}')
        argv += ["--contract", str(contract)]
    assert main(argv) == 0
    protocol = json.loads(capsys.readouterr().out)["delta_protocol"]
    assert (protocol["min_reader_version"], protocol["min_writer_version"]) == (1, 3)
    assert protocol["writer_features"] == ["checkConstraints"]
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize(
    "feature,versions,features",
    [
        ("column_mapping=name", (2, 5), ["columnMapping"]),
        ("deletion_vectors=true", (3, 7), ["deletionVectors"]),
    ],
)
def test_explicit_feature_preview_without_derived_checks_stays_exact(
    tmp_path, monkeypatch, capsys, feature, versions, features
):
    args = arguments(tmp_path, feature)
    args.sql_constraints = "keys"
    args.contract = None
    monkeypatch.setattr(delta, "_location", lambda *args, **kwargs: pytest.fail("no storage"))
    assert generation._generate_schema(args, lambda: schema(business_rule=False), {}) == 0
    protocol = json.loads(capsys.readouterr().out)["delta_protocol"]
    assert (protocol["min_reader_version"], protocol["min_writer_version"]) == versions
    assert protocol["reader_features"] == features
    assert protocol["writer_features"] == features
    assert not (tmp_path / "absent").exists()
