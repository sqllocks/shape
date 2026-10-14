"""Pin the profile-db stdout receipt and --json summary key sets."""

from __future__ import annotations

import argparse
import json

import pytest
from shape_sqlserver import ProfileDbCommand
from shape_sqlserver.testing import scenario

pytestmark = pytest.mark.contract


@pytest.mark.parametrize(
    "write_artifact", [False, True], ids=["summary-only", "artifact-and-summary"]
)
@pytest.mark.parametrize("sample_rows", [0, 50], ids=["catalog-only", "sampled"])
def test_profile_db_json_key_sets(monkeypatch, tmp_path, capsys, write_artifact, sample_rows):
    """Adding or removing a public output key must break the compatibility pin."""
    monkeypatch.setattr("shape_sqlserver.profiler.connect", lambda text, creds: scenario("retail"))
    command = ProfileDbCommand()
    parser = argparse.ArgumentParser()
    command.configure(parser)
    summary_path = tmp_path / "summary.json"
    argv = [
        "--connection-string",
        "Server=x",
        "--tables",
        "orders,customer",
        "--sample-rows",
        str(sample_rows),
        "--json",
        str(summary_path),
    ]
    if write_artifact:
        argv.extend(["-o", str(tmp_path / "database.shape")])
    assert command.run(parser.parse_args(argv)) == 0

    # The flat stdout document is the receipt shown in docs/plugins/sqlserver.md.
    receipt = json.loads(capsys.readouterr().out)
    assert set(receipt) == (
        {"written", "shape_content_id", "tables", "relationships"}
        if write_artifact
        else {"tables", "relationships"}
    )
    assert receipt["tables"] == 2
    assert receipt["relationships"] == 1

    # --json writes the nested profile summary, rather than the stdout receipt.
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert set(summary) == {"name", "row_count", "tables", "relationships"}
    assert set(summary["tables"]) == {"orders", "customer"}
    for table in summary["tables"].values():
        assert set(table) == {"name", "row_count", "primary_key", "columns"}
        assert table["columns"]
        for column in table["columns"].values():
            assert set(column) == {
                "dtype",
                "null_rate",
                "cardinality",
                "is_unique",
                "is_primary_key",
                "is_foreign_key",
                "fk_ref_table",
                "distribution",
                "pattern",
                "min",
                "max",
                "mean",
                "std",
            }
    assert len(summary["relationships"]) == 1
    for relationship in summary["relationships"]:
        assert set(relationship) == {
            "name",
            "parent",
            "child",
            "parent_columns",
            "child_columns",
            "type",
            "evidence",
        }
