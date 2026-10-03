"""AUD-scenario: tests for paths of the scenario package that no test reached (behaviour as is)."""

from __future__ import annotations

from dataclasses import replace

import pyarrow as pa
import pytest

from shape.scenario import GSLParser, PackError, PackLoader
from shape.scenario.resolve import spec_domain, spec_pack
from shape.scenario.runner import _run_gate
from shape.scenario.validator import PackValidationResult

# ---- the validation summary --------------------------------------------------------------------


def test_the_summary_lists_errors_and_warnings_and_the_verdict():
    assert PackValidationResult().summary() == "Pack validation: PASS"
    warned = PackValidationResult(warnings=["w"]).summary()
    assert warned == "Warnings (1):\n  WARN:  w\nPack validation: PASS (1 warnings)"
    failed = PackValidationResult(errors=["e"], warnings=["w"]).summary().splitlines()
    assert failed == [
        "Errors (1):",
        "  ERROR: e",
        "Warnings (1):",
        "  WARN:  w",
        "Pack validation: FAIL (1 errors)",
    ]


# ---- the spec's schema and pack ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("schema", "message"),
    [
        (None, "no schema section"),
        ({"type": "domain"}, "schema.domain is missing"),
        ({"type": "schema_file"}, "schema.path is missing"),
        ({"type": "schema_file", "path": "nope.json"}, "schema file not found"),
        ({"type": "warehouse"}, "unknown schema type 'warehouse'"),
    ],
)
def test_spec_domain_says_what_is_missing(tmp_path, schema, message):
    doc = {"version": 1} if schema is None else {"version": 1, "schema": schema}
    spec = GSLParser().parse_dict(doc, tmp_path)
    with pytest.raises(PackError, match=message):
        spec_domain(spec)


def test_spec_pack_needs_a_scenario_pack(tmp_path):
    with pytest.raises(PackError, match="no scenario.pack"):
        spec_pack(GSLParser().parse_dict({"version": 1, "scenario": {"scale": "small"}}))


# ---- the sections of a pack --------------------------------------------------------------------


def test_every_file_drop_and_stream_section_is_read():
    pack = PackLoader().parse(
        {
            "id": "x",
            "kind": "stream",
            "file_drop": {
                "manifest": {"enabled": False, "name": "m.json"},
                "done_flag": {"enabled": False},
                "lateness": {"enabled": True, "probability": 0.1, "max_days_late": 2},
                "duplicates": {"enabled": True, "probability": 0.2},
                "backfill": {"enabled": True, "max_days_back": 3},
            },
            "streaming": {
                "envelope": {"schemaVersion": "2.0", "fields": ["a"]},
                "ordering": {"out_of_order_probability": 0.5, "max_delay_seconds": 9},
                "replay": {"enabled": True, "window_minutes": 5},
                "anomalies": {"enabled": True, "types": ["spike"]},
            },
            "failure_injection": {
                "enabled": True,
                "schema_drift": {"enabled": True, "mode": "breaking", "breaking_change_day": 4},
            },
        }
    )
    fd, st, fi = pack.file_drop, pack.streaming, pack.failure_injection
    assert fd is not None and st is not None and fi is not None
    assert fd.manifest is not None and fd.manifest.name == "m.json"
    assert fd.lateness is not None and fd.lateness.max_days_late == 2
    assert fd.duplicates is not None and fd.duplicates.probability == 0.2
    assert fd.backfill is not None and fd.backfill.max_days_back == 3
    assert st.envelope is not None and st.envelope.schemaVersion == "2.0"
    assert st.ordering is not None and st.ordering.max_delay_seconds == 9
    assert st.replay is not None and st.replay.window_minutes == 5
    assert st.anomalies is not None and st.anomalies.types == ["spike"]
    assert fi.schema_drift is not None and fi.schema_drift.breaking_change_day == 4
    assert pack.extra_keys == []


# ---- the gates on tables that fail them --------------------------------------------------------


@pytest.fixture(scope="module")
def generated(retail):
    from shape.generation.engine import Engine

    return Engine(retail.schema, scale="small", seed=3).generate()


def test_schema_conformance_fails_on_a_missing_table_or_column(generated):
    tables = dict(generated.tables)
    tables.pop("customer")
    passed, why = _run_gate("schema_conformance", replace(generated, tables=tables))
    assert not passed and why == "table customer was not generated"
    tables = dict(generated.tables)
    tables["customer"] = tables["customer"].drop_columns(["customer_id"])
    passed, why = _run_gate("schema_conformance", replace(generated, tables=tables))
    assert not passed and why == "customer lacks columns customer_id"


def test_null_check_fails_on_a_null_in_a_column_without_nulls(generated):
    tdef = generated.schema.tables["customer"]
    column = next(
        c for c, col in tdef.columns.items() if not col.nullable and not col.null_rate > 0
    )
    tables = dict(generated.tables)
    table = tables["customer"]
    index = table.column_names.index(column)
    nulls = pa.nulls(table.num_rows, type=table.schema.field(index).type)
    tables["customer"] = table.set_column(index, column, nulls)
    passed, why = _run_gate("null_check", replace(generated, tables=tables))
    assert not passed and why == f"customer.{column} has nulls"


def test_uniqueness_fails_on_a_duplicate_key(generated):
    tables = dict(generated.tables)
    table = tables["customer"]
    tables["customer"] = pa.concat_tables([table, table.slice(0, 1)])
    passed, why = _run_gate("uniqueness", replace(generated, tables=tables))
    assert not passed and why == "customer has duplicate primary keys"


def test_an_unknown_gate_fails(generated):
    passed, why = _run_gate("vibes", generated)
    assert not passed and why.startswith("unknown gate (known: ")
