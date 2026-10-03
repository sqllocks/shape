"""ISS-verify (issue #32): ``shape verify --config`` runs the range, temporal, drift and file
gates, and the gate schema names the key a relationship lacks."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from shape.cli.main import main
from shape.quality import (
    GateRunner,
    GateSchema,
    GateSchemaError,
    ValidationContext,
    VerifyConfig,
    VerifyConfigError,
    VerifyReport,
    VerifyRunner,
    load_tables,
)

NOW = datetime.now(UTC).replace(microsecond=0)


def write_data(d):
    d.mkdir()
    ts = [NOW - timedelta(days=1), NOW - timedelta(days=300), NOW + timedelta(days=5)]
    pq.write_table(
        pa.table(
            {
                "id": [1, 2, 3],
                "amount": [10.0, -2.5, 4.0],
                "placed": pa.array(ts, pa.timestamp("us", tz="UTC")),
                "shipped": pa.array(
                    [ts[0] + timedelta(days=1), ts[1] - timedelta(days=1), ts[2]],
                    pa.timestamp("us", tz="UTC"),
                ),
            }
        ),
        d / "orders.parquet",
    )
    return d


def doc(**rules):
    return {"format": "shape-verify-config", "version": 1, **rules}


def write_config(tmp_path, **rules):
    p = tmp_path / "verify.json"
    p.write_text(json.dumps(doc(**rules)))
    return p


FULL = {
    "ranges": {"orders.amount": {"min": 0}},
    "date_range": {"start": (NOW - timedelta(days=30)).isoformat()},
    "no_future": ["orders.placed"],
    "ordering": [{"table": "orders", "start": "placed", "end": "shipped"}],
    "baseline": {"orders": {"columns": {"id": "int64", "amount": "int64"}}},
}


def test_the_cli_runs_the_config_gates_like_the_python_api(tmp_path, capsys):
    data = write_data(tmp_path / "d")
    cfg = write_config(tmp_path, **FULL, check_data_files=True)
    assert main(["verify", str(data), "--config", str(cfg)]) == 1
    out = capsys.readouterr()
    for gate in ("range_constraint", "temporal_consistency", "schema_drift", "file_format"):
        assert gate in out.out
    assert "values below minimum 0" in out.err
    assert "dates before" in out.err and "in the future" in out.err
    assert "type changed" in out.err and "'end' < 'start'" not in out.err
    # the same settings through the Python API give the same errors
    tables = load_tables(data)
    ctx = ValidationContext(tables=tables, config=dict(FULL))
    api = {r.gate_name: r for r in GateRunner().run_all(ctx)}
    cli = VerifyRunner(None, False, str(data), None, VerifyConfig.from_dict(doc(**FULL))).run(
        tables
    )
    for g in cli.gate_results:
        assert g.errors == api[g.gate_name].errors, g.gate_name


def test_a_config_without_a_schema_runs_only_its_gates(tmp_path):
    data = write_data(tmp_path / "d")
    cfg = write_config(tmp_path, ranges={"orders.amount": {"min": -5}})
    r = VerifyRunner(
        None, config=VerifyConfig.from_dict(doc(ranges={"orders.amount": {"min": -5}}))
    ).run(load_tables(data))
    assert [g.gate_name for g in r.gate_results] == ["range_constraint"] and r.passed
    assert main(["verify", str(data), "--config", str(cfg)]) == 0


def test_the_file_gate_reads_the_listed_files(tmp_path, capsys):
    data = write_data(tmp_path / "d")
    (tmp_path / "empty.csv").write_text("")
    cfg = write_config(
        tmp_path, file_paths=[str(tmp_path / "empty.csv"), str(data / "orders.parquet")]
    )
    assert main(["verify", str(data), "--config", str(cfg)]) == 1
    assert "File is empty" in capsys.readouterr().err


def test_the_report_names_the_config(tmp_path):
    data = write_data(tmp_path / "d")
    cfg = write_config(tmp_path, ranges={"orders.amount": {"min": 0}})
    rep = tmp_path / "r.json"
    assert main(["verify", str(data), "--config", str(cfg), "-o", str(rep)]) == 1
    assert json.loads(rep.read_text(encoding="utf-8"))["config_path"] == str(cfg)
    md = tmp_path / "r.md"
    main(["verify", str(data), "--config", str(cfg), "-o", str(md)])
    assert f"--config {cfg}" in md.read_text(encoding="utf-8")
    assert (
        "range_constraint"
        in VerifyReport(
            VerifyRunner(
                None, config=VerifyConfig.from_dict(doc(ranges={"orders.amount": {"min": 0}}))
            ).run(load_tables(data))
        ).to_markdown()
    )


@pytest.mark.parametrize(
    "bad, message",
    [
        ({"rangez": {}}, "unknown key 'rangez'"),
        ({"ranges": {"amount": {"min": 0}}}, 'must be "table.column"'),
        ({"ranges": {"o.a": {"minimum": 0}}}, 'unknown key "minimum"'),
        ({"ranges": {"o.a": {"min": "0"}}}, "must be a number"),
        ({"date_range": {"start": "not a date"}}, "not an ISO 8601"),
        ({"no_future": "o.a"}, "list"),
        ({"ordering": [{"table": "o", "start": "a"}]}, 'ordering[0]: missing required key "end"'),
        ({"baseline": {"o": {"cols": {}}}}, 'baseline["o"]'),
        ({"distribution_alpha": 2}, "between 0 and 1"),
        ({"file_paths": "a.csv"}, "list"),
        ({"check_data_files": "yes"}, "true or false"),
    ],
)
def test_a_bad_config_is_refused_with_the_rule(bad, message):
    with pytest.raises(VerifyConfigError, match=re.escape(message)):
        VerifyConfig.from_dict(doc(**bad))


def test_the_config_is_a_versioned_document(tmp_path, capsys):
    with pytest.raises(VerifyConfigError, match="expected format"):
        VerifyConfig.from_dict({"ranges": {}})
    with pytest.raises(VerifyConfigError, match="version 2"):
        VerifyConfig.from_dict({"format": "shape-verify-config", "version": 2})
    data = write_data(tmp_path / "d")
    bad = tmp_path / "bad.json"
    bad.write_text("{")
    assert main(["verify", str(data), "--config", str(bad)]) == 2
    assert main(["verify", str(data), "--config", str(tmp_path / "missing.json")]) == 2
    assert "shape:" in capsys.readouterr().err


def test_a_relationship_without_a_name_names_the_missing_key():
    rel = {"parent": "a", "child": "b", "parent_columns": ["x"], "child_columns": ["x"]}
    with pytest.raises(GateSchemaError, match=r'relationships\[0\]: missing required key "name"'):
        GateSchema.from_dict(
            {"format": "shape-gates", "version": 1, "tables": {}, "relationships": [rel]}
        )


def test_temporal_rules_that_cannot_apply_warn_instead_of_passing_silently(tmp_path):
    d = tmp_path / "csv"
    d.mkdir()
    (d / "orders.csv").write_text("id,placed\n1,2020-01-01T00:00:00\n")
    cfg = VerifyConfig.from_dict(
        doc(date_range={"start": "2026-01-01"}, no_future=["orders.placed"])
    )
    (gate,) = VerifyRunner(None, config=cfg).run(load_tables(d)).gate_results
    assert gate.passed
    assert any("date_range checked nothing" in w for w in gate.warnings)
    assert any("orders.placed: not a timestamp column" in w for w in gate.warnings)
