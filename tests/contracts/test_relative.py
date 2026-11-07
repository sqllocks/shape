"""Relative bounds use an explicit batch date and calendar days, with timezone fidelity."""

import datetime as dt
import json
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.cli.main import main
from shape.contracts.emit import emit
from shape.contracts.v1 import ContractError, validate_contract


def rules(bound: str = "min", offset: Any = 0, **extra: Any) -> dict[str, Any]:
    return {"columns": {"order_date": {bound: {"as_of_offset_days": offset}}}, **extra}


def checked(
    values: list[Any],
    contract: dict[str, Any] | None = None,
    as_of: Any = "2026-10-07",
    exact: bool = False,
) -> Any:
    table = pa.table({"order_date": values})
    return shape.check(
        shape.profile(table),
        contract or rules(),
        as_of=as_of,
        data={"batch": table} if exact else None,
    )


@pytest.mark.parametrize("exact", [False, True])
def test_synthetic_backdated_orders(exact: bool) -> None:
    current = dt.datetime(2026, 10, 7, 12)
    backdated = [dt.datetime(2026, 9, 27) + dt.timedelta(days=i % 10) for i in range(104)]
    result = checked([current] * 1974 + backdated, exact=exact)
    assert not result.passed
    violation = result.violations[0]
    assert violation["observed"].startswith("2026-09-27")
    assert violation["expected"] == {
        "as_of_offset_days": 0,
        "as_of": "2026-10-07",
        "bound": "2026-10-07T00:00:00+00:00",
    }
    assert result.as_of == "2026-10-07"
    assert result.to_dict()["as_of"] == "2026-10-07"
    assert checked([dt.datetime(2026, 10, 6, 12)] * 2078, as_of="2026-10-06", exact=exact).passed


@pytest.mark.parametrize(
    "value,passed", [("2026-10-06T00:00:00", True), ("2026-10-05T23:59:59", False)]
)
def test_min_offset(value: str, passed: bool) -> None:
    assert checked([value], rules(offset=-1)).passed is passed


@pytest.mark.parametrize(
    "value,passed", [("2026-10-07T23:59:59.999999", True), ("2026-10-08T00:00:00", False)]
)
def test_max_entire_day(value: str, passed: bool) -> None:
    assert checked([value], rules("max")).passed is passed


@pytest.mark.parametrize(
    "value,passed", [(dt.date(2026, 10, 7), True), (dt.date(2026, 10, 6), False)]
)
def test_date_column(value: dt.date, passed: bool) -> None:
    assert checked([value], as_of=dt.date(2026, 10, 7)).passed is passed


@pytest.mark.parametrize("exact", [False, True])
@pytest.mark.parametrize(
    "value,passed",
    [
        ("2026-10-07T03:59:59+00:00", False),
        ("2026-10-07T04:00:00+00:00", True),
        ("2026-10-07T00:00:00", True),
        ("2026-10-06T23:59:59", False),
    ],
)
def test_new_york_day(value: str, passed: bool, exact: bool) -> None:
    assert (
        checked(
            [dt.datetime.fromisoformat(value)],
            rules(as_of_timezone="America/New_York"),
            exact=exact,
        ).passed
        is passed
    )


@pytest.mark.parametrize(
    "value,passed", [("2026-10-08T03:59:59+00:00", True), ("2026-10-08T04:00:00+00:00", False)]
)
def test_new_york_max(value: str, passed: bool) -> None:
    assert (
        checked(
            [dt.datetime.fromisoformat(value)], rules("max", as_of_timezone="America/New_York")
        ).passed
        is passed
    )


@pytest.mark.parametrize("value", [1.5, "1", True, None])
def test_invalid_offset(value: Any) -> None:
    with pytest.raises(ContractError, match="min.*order_date.*integer"):
        validate_contract(rules(offset=value))


def test_unknown_bound_key() -> None:
    contract = rules()
    contract["columns"]["order_date"]["min"]["extra"] = 0
    with pytest.raises(ContractError, match="min.*order_date"):
        validate_contract(contract)


def test_invalid_zone_and_missing_asof() -> None:
    with pytest.raises(ContractError, match="as_of_timezone"):
        validate_contract(rules(as_of_timezone="Unknown/Timezone"))
    with pytest.raises(ContractError, match="--as-of"):
        checked(["2026-10-07"], as_of=None)
    with pytest.raises(ContractError, match="as_of"):
        checked(["2026-10-07"], as_of="2026-02-30")


def test_redacted_extreme_blind() -> None:
    from shape.privacy.redact import CaptureConfig, redact_profile

    profile = redact_profile(
        shape.profile(pa.table({"order_date": [dt.datetime(2026, 10, 6)]})),
        CaptureConfig(classifications={"order_date": "CONFIDENTIAL"}),
    )
    result = shape.check(profile, rules(), as_of="2026-10-07")
    assert not result.passed
    assert result.not_evaluable[0]["rule"] == "min"
    assert not result.violations


@pytest.mark.parametrize("exact", [False, True])
def test_unparseable_observed_is_violation(exact: bool) -> None:
    assert not checked(["not-a-date"], exact=exact).passed


def test_relative_strength_and_constants() -> None:
    contract = rules()
    contract["columns"]["order_date"]["strength"] = {"min": "soft"}
    result = checked(["2026-10-06"], contract)
    assert result.passed and result.warnings[0]["strength"] == "soft"
    table = pa.table({"order_date": ["2026-10-06"]})
    assert shape.check(
        shape.profile(table), {"columns": {"order_date": {"min": "2026-10-05"}}}
    ).passed


@pytest.mark.parametrize("target", ["ddl", "gx", "jsonschema", "pandera"])
def test_emitter_gap(target: str) -> None:
    contract = rules()
    contract["columns"]["order_date"]["max"] = {"as_of_offset_days": 1}
    emitted = emit(contract, target, **({"dialect": "postgres"} if target == "ddl" else {}))
    assert {n["rule"] for n in emitted.not_expressed} >= {"min", "max"}
    assert "2026-" not in emitted.text


def test_cli_asof(tmp_path: Path) -> None:
    data = tmp_path / "batch.parquet"
    table = pa.table({"order_date": [dt.datetime(2026, 10, 6)]})
    pq.write_table(table, data)
    profile = tmp_path / "batch.shape"
    shape.save(shape.profile(table), profile, capture="full")
    contract = tmp_path / "contract.json"
    contract.write_text(json.dumps(rules()))
    args = ["check", str(profile), str(contract), "--data", str(data)]
    assert main(args) == 2  # type: ignore[no-untyped-call]
    assert main([*args, "--as-of", "2026-10-07"]) == 1  # type: ignore[no-untyped-call]
    assert main([*args, "--as-of", "2026-10-06"]) == 0  # type: ignore[no-untyped-call]


def test_dataset_relative_dates() -> None:
    tables = {
        "a": pa.table({"order_date": ["2026-10-07"]}),
        "b": pa.table({"order_date": ["2026-10-06"]}),
    }
    result = shape.check(
        shape.profile(tables),
        {"tables": {name: rules() for name in tables}, "as_of_timezone": "UTC"},
        as_of="2026-10-07",
        data=tables,
    )
    assert [v["column"] for v in result.violations] == ["b.order_date"]


def test_dst_day_uses_next_local_midnight() -> None:
    contract = rules("max", as_of_timezone="America/New_York")
    assert checked(
        [dt.datetime(2026, 11, 2, 4, 59, 59, tzinfo=dt.UTC)], contract, as_of="2026-11-01"
    ).passed
    assert not checked(
        [dt.datetime(2026, 11, 2, 5, tzinfo=dt.UTC)], contract, as_of="2026-11-01"
    ).passed


def test_data_extreme_replaces_profile_extreme() -> None:
    profile = shape.profile(pa.table({"order_date": [dt.datetime(2026, 10, 1)]}))
    current = pa.table({"order_date": [dt.datetime(2026, 10, 7)]})
    assert not shape.check(profile, rules(), as_of="2026-10-07").passed
    assert shape.check(profile, rules(), as_of="2026-10-07", data={"batch": current}).passed


@pytest.mark.parametrize("target", ["gx", "jsonschema"])
def test_relative_metadata_round_trip_preserves_zone(target: str) -> None:
    from shape.contracts.emit import contract_from

    original = rules(as_of_timezone="America/New_York")
    assert contract_from(target, emit(original, target).text, use_meta=True) == original
    dataset = {"as_of_timezone": "America/New_York", "tables": {"batch": rules()}}
    restored = contract_from(target, emit(dataset, target, table="batch").text, use_meta=True)
    assert restored["as_of_timezone"] == "America/New_York"


@pytest.mark.parametrize("target", ["ddl", "gx", "jsonschema", "pandera"])
def test_timezone_context_is_an_explicit_gap(target: str) -> None:
    emitted = emit(rules(as_of_timezone="America/New_York"), target)
    assert any(n["rule"] == "as_of_timezone" for n in emitted.not_expressed)
