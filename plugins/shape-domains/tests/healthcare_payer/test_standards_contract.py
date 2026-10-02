"""The tables meet the standard-outputs contract (a snapshot of the standards lane's input contract).

The snapshot is ``standards_contract_snapshot.json``, taken from the standards lane's
``contract.py``.  The check is the contract's own rule: every required column exists, optional
columns are used under the same name, types coerce, and key columns are unique and not null.
When the lanes are merged, ``tests/test_contract_current.py`` of the standards plugin runs the
same tables through the real ``coerce`` (see the lane status file for the command)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pytest

SNAPSHOT = json.loads((Path(__file__).parent / "standards_contract_snapshot.json").read_text())


def _compatible(have: pa.DataType, want: str) -> bool:
    if want == "string":
        return pa.types.is_string(have) or pa.types.is_large_string(have)
    if want == "date32[day]":
        return pa.types.is_date32(have) or pa.types.is_timestamp(have)
    if want == "double":
        return pa.types.is_floating(have) or pa.types.is_integer(have)
    if want == "int64":
        return pa.types.is_integer(have)
    if want == "bool":
        return pa.types.is_boolean(have)
    raise AssertionError(want)


@pytest.mark.parametrize("table", sorted(SNAPSHOT["tables"]))
def test_table_has_every_required_column_with_a_compatible_type(data, table):
    t = data.tables[table]
    names = set(t.column_names)
    for col in SNAPSHOT["tables"][table]:
        if col["required"]:
            assert col["name"] in names, f"{table}.{col['name']} is required"
        if col["name"] in names:
            assert _compatible(t.schema.field(col["name"]).type, col["type"]), (
                f"{table}.{col['name']}: {t.schema.field(col['name']).type} vs {col['type']}"
            )


@pytest.mark.parametrize("table", sorted(SNAPSHOT["keys"]))
def test_key_columns_are_unique_and_not_null(data, table):
    t = data.tables[table]
    keys = SNAPSHOT["keys"][table]
    for k in keys:
        assert t.column(k).null_count == 0, f"{table}.{k} has nulls"
    grouped = t.group_by(keys).aggregate([([], "count_all")])
    assert grouped.num_rows == t.num_rows, f"{table}: duplicate key {keys}"


def test_the_optional_contract_columns_are_filled_where_the_data_has_them(data):
    claims = data.tables["medical_claim"]
    for col in (
        "total_allowed",
        "total_paid",
        "member_copay",
        "claim_status",
        "received_date",
        "plan_id",
    ):
        assert claims.column(col).null_count == 0, col
    assert any(v for v in claims.column("attending_npi").to_pylist())
    assert {v for v in claims.column("admission_type_code").to_pylist() if v} <= {"1", "3"}
    dx = data.tables["claim_diagnosis"]
    assert all("." not in c for c in dx.column("icd10_code").to_pylist())
    assert all(
        a.replace(".", "") == b
        for a, b in zip(
            dx.column("diagnosis_code").to_pylist(),
            dx.column("icd10_code").to_pylist(),
            strict=True,
        )
    )
    assert {v for v in data.tables["pharmacy_claim"].column("dea_schedule").to_pylist() if v} <= {
        "CII",
        "CIII",
        "CIV",
    }
    assert {v for v in data.tables["eligibility"].column("plan_type").to_pylist()} == {
        "COMMERCIAL",
        "MEDICARE_ADVANTAGE",
        "MEDICAID",
    }
    assert {v for v in data.tables["eligibility"].column("coverage_level").to_pylist()} <= {
        "IND",
        "FAM",
        "E1D",
        "ESP",
        "ECH",
    }


def test_claim_money_balances_as_the_835_writer_requires(data):
    # the standards lane's 835 writer needs: line allowed = paid + member responsibility, and the
    # header paid = sum of line paid; the claim total billed = sum of line billed
    lines = {}
    for r in data.tables["medical_claim_line"].to_pylist():
        lines.setdefault(r["claim_id"], []).append(r)
    for c in data.tables["medical_claim"].to_pylist():
        ls = lines[c["claim_id"]]
        assert round(sum(x["billed_amount"] for x in ls), 2) == round(c["total_billed"], 2)
        assert round(sum(x["paid_amount"] for x in ls), 2) == round(c["total_paid"], 2)
        for x in ls:
            assert round(x["allowed_amount"], 2) == round(
                x["paid_amount"] + x["copay"] + x["coinsurance"] + x["deductible"], 2
            )
