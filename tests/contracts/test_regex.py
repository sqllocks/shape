"""Data-backed RE2 contract rules: validation, privacy, rates and CLI behavior."""

import json
import time
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.cli.main import main
from shape.contracts.emit import contract_from, emit
from shape.contracts.emit._common import PATTERNS
from shape.contracts.v1 import CheckResult, ContractError, validate_contract


def contract(rule: Any, **extra: Any) -> dict[str, Any]:
    return {"columns": {"code": {"regex": rule, **extra}}}


def check(values: list[Any], rule: Any = r"\d{3}", **extra: Any) -> CheckResult:
    table = pa.table({"code": values})
    return shape.check(shape.profile(table), contract(rule, **extra), data={"data": table})


@pytest.mark.parametrize(
    "values,passed",
    [
        (["123", "456"], True),
        (["123", "1234"], False),
        ([None, "123"], True),
        ([None, None], True),
        ([], True),
    ],
)
def test_whole_value_and_nulls(values: list[Any], passed: bool) -> None:
    assert check(values).passed is passed


@pytest.mark.parametrize("count,passed", [(90, True), (89, False)])
def test_rate_boundary(count: int, passed: bool) -> None:
    assert (
        check(
            ["123"] * count + ["bad"] * (100 - count),
            {"expression": r"\d{3}", "min_match_rate": 0.9},
        ).passed
        is passed
    )


@pytest.mark.parametrize(
    "rule",
    [
        r"(a)\1",
        "(?=a)a",
        "[",
        "",
        {},
        {"expression": ""},
        {"expression": "a", "extra": 1},
        *[
            {"expression": "a", "min_match_rate": r}
            for r in [1.5, float("nan"), float("inf"), "0.9", True, -0.1]
        ],
    ],
)
def test_errors_before_data_read(rule: Any, tmp_path: Path) -> None:
    with pytest.raises(ContractError, match="regex.*code"):
        shape.check(
            shape.profile(pa.table({"code": ["123"]})),
            contract(rule),
            data=tmp_path / "missing.csv",
        )
    with pytest.raises(ContractError, match="regex.*code"):
        validate_contract(contract(rule))


def test_complete_profile_does_not_need_data() -> None:
    assert shape.check(shape.profile(pa.table({"code": ["123"]})), contract(r"\d{3}")).passed


def test_safe_aggregates_and_strength() -> None:
    result = check(["123", "PRIVATE-FAILURE"], strength={"regex": "soft"})
    assert result.passed
    assert result.warnings[0]["observed"] == {
        "match_rate": 0.5,
        "non_matching_count": 1,
        "tested_count": 2,
        "non_matching_values": [],
    }
    assert result.warnings[0]["expected"] == {"expression": r"\d{3}", "min_match_rate": 1.0}
    assert "PRIVATE" not in json.dumps(result.to_dict())
    assert not check(["bad"], strength="hard").passed


def test_tables_and_missing_data() -> None:
    tables = {"a": pa.table({"code": ["123"]}), "b": pa.table({"code": ["bad"]})}
    rules = {"tables": {name: contract(r"\d{3}") for name in tables}}
    profile = shape.profile(tables)
    result = shape.check(profile, rules, data=tables)
    assert [v["column"] for v in result.violations] == ["b.code"]
    assert not shape.check(profile, rules).passed
    with pytest.raises(ContractError, match="b.code"):
        shape.check(profile, rules, data={"a": tables["a"]})


def test_profiler_spelling_and_ambiguous_nested() -> None:
    assert check([123, 456], r"\d{3}").passed
    assert check([True, False], "True|False").passed
    with pytest.raises(ContractError, match="regex.*code"):
        check([["123"]])


def test_linear_time() -> None:
    start = time.monotonic()
    assert not check(["a" * 100000 + "!"], "(a+)+$").passed
    assert time.monotonic() - start < 5


@pytest.mark.parametrize("values,status", [(["123"], 0), (["bad"], 1)])
def test_cli(tmp_path: Path, values: list[Any], status: int) -> None:
    table = pa.table({"code": values})
    data = tmp_path / "data.parquet"
    pq.write_table(table, data)
    profile = tmp_path / "data.shape"
    shape.save(shape.profile(table), profile)
    rules = tmp_path / "contract.json"
    rules.write_text(json.dumps(contract(r"\d{3}")))
    assert main(["check", str(profile), str(rules), "--data", str(data)]) == status  # type: ignore[no-untyped-call]


@pytest.mark.parametrize(
    "target,needle",
    [
        ("ddl", " ~ "),
        ("gx", '"mostly": 1.0'),
        ("jsonschema", '"pattern"'),
        ("pandera", "Check.str_matches"),
    ],
)
def test_emit_mapping(target: str, needle: str) -> None:
    result = emit(
        contract("[A-Z]{3}", dtype="string"),
        target,
        **({"dialect": "postgres"} if target == "ddl" else {}),
    )
    assert needle in result.text
    assert "^(?:[A-Z]{3})$" in result.text
    assert not any(n["rule"] == "regex" for n in result.not_expressed)


@pytest.mark.parametrize("target", ["ddl", "jsonschema", "pandera"])
def test_rate_gap(target: str) -> None:
    result = emit(contract({"expression": "a", "min_match_rate": 0.9}), target)
    assert any(n["rule"] == "regex" for n in result.not_expressed)
    if target == "jsonschema":
        assert (
            json.loads(result.text)["properties"]["code"]["x-shape"]["regex"]["min_match_rate"]
            == 0.9
        )


@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse"])
def test_ddl_gap(dialect: str) -> None:
    result = emit(contract("a"), "ddl", dialect=dialect)
    assert any(n["rule"] == "regex" for n in result.not_expressed)
    assert "CHECK" not in result.text


def test_mysql_mapping() -> None:
    assert "REGEXP" in emit(contract("a"), "ddl", dialect="mysql").text


@pytest.mark.parametrize("target", ["gx", "jsonschema"])
def test_round_trip(target: str) -> None:
    original = contract("[A-Z]{3}")
    restored = contract_from(target, emit(original, target).text)
    assert check(["ABC"], restored["columns"]["code"]["regex"]).passed
    assert not check(["ABCD"], restored["columns"]["code"]["regex"]).passed
    text = (
        json.dumps({"properties": {"code": {"pattern": PATTERNS["email"]}}})
        if target == "jsonschema"
        else json.dumps(
            {
                "expectations": [
                    {
                        "type": "expect_column_values_to_match_regex",
                        "kwargs": {"column": "code", "regex": PATTERNS["email"]},
                    }
                ]
            }
        )
    )
    assert contract_from(target, text)["columns"]["code"]["pattern"] == "email"


def test_gx_rate_round_trip() -> None:
    original = contract({"expression": "a", "min_match_rate": 0.9})
    assert contract_from("gx", emit(original, "gx").text) == original


def test_pattern_and_regex_independent() -> None:
    result = check(["123"], pattern="email")
    assert [v["rule"] for v in result.violations] == ["pattern"]
    emitted = emit(contract("a", pattern="email"), "jsonschema")
    prop = json.loads(emitted.text)["properties"]["code"]
    assert prop["pattern"] == PATTERNS["email"]
    assert prop["allOf"] == [{"pattern": "^(?:a)$"}]


def test_cli_validates_regex_before_opening_data(tmp_path: Path, capsys: Any) -> None:
    profile = tmp_path / "p.shape"
    shape.save(shape.profile(pa.table({"code": ["123"]})), profile)
    rules = tmp_path / "contract.json"
    rules.write_text(json.dumps(contract(r"(a)\1")))
    assert main(["check", str(profile), str(rules), "--data", str(tmp_path / "missing.csv")]) == 2  # type: ignore[no-untyped-call]
    captured = capsys.readouterr()
    assert "regex" in captured.err and "code" in captured.err
    assert "Path not found" not in captured.err


@pytest.mark.parametrize("target", ["gx", "jsonschema"])
def test_normalized_round_trip(target: str) -> None:
    from shape.contracts.emit import expressible

    original = contract("[A-Z]+", pattern="email")
    assert contract_from(target, emit(original, target).text) == expressible(original, target)


@pytest.mark.parametrize("rate,passed", [(0, True), (1, False)])
def test_rate_endpoints(rate: int, passed: bool) -> None:
    assert check(["bad"], {"expression": r"\d{3}", "min_match_rate": rate}).passed is passed


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_values_are_never_silently_ignored(value: float) -> None:
    with pytest.raises(ContractError, match="regex.*code"):
        check([value], ".*")


def test_zoned_timestamp_keeps_offset() -> None:
    import datetime

    assert check(
        [datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC)], r"2026-01-01 00:00:00\+00:00"
    ).passed


def test_import_allof_boolean_schema_does_not_crash() -> None:
    text = json.dumps({"properties": {"code": {"pattern": "a", "allOf": [True]}}})
    assert contract_from("jsonschema", text)["columns"]["code"]["regex"]["expression"] == "a"


@pytest.mark.parametrize("bad,passed", [(0, True), (10, True), (11, False)])
def test_profile_exact_counts(bad: int, passed: bool) -> None:
    table = pa.table({"code": ["123"] * (100 - bad) + ["bad"] * bad + [None] * 5})
    result = shape.check(
        shape.profile(table), contract({"expression": r"\d{3}", "min_match_rate": 0.9})
    )
    assert result.passed is passed
    assert not result.not_evaluable
    if bad == 11:
        assert result.violations[0]["observed"]["tested_count"] == 100
        assert result.violations[0]["observed"]["non_matching_count"] == 11


def test_incomplete_profile_blind_but_known_failure_definite() -> None:
    from shape.profile.reference.profile import Profile

    table = pa.table({"code": ["123"] * 90 + ["bad"] * 10})
    document = shape.profile(table).to_dict()
    document["columns"]["code"]["value_counts_ext"] = {"123": 0.9}
    profile = Profile(document)
    result = shape.check(profile, contract(r"\d{3}"))
    assert not result.passed and result.not_evaluable[0]["rule"] == "regex"
    assert "--data" in result.not_evaluable[0]["reason"]
    assert not shape.check(profile, contract(r"\d{3}"), data={"batch": table}).passed
    document["columns"]["code"]["value_counts_ext"] = {"bad": 0.1}
    result = shape.check(Profile(document), contract(r"\d{3}"))
    assert result.violations and not result.not_evaluable
    result = shape.check(
        Profile(document), contract({"expression": r"\d{3}", "min_match_rate": 0.9})
    )
    assert result.not_evaluable and not result.violations


@pytest.mark.parametrize("exact", [False, True])
def test_sample_cap_and_safe_policy(exact: bool) -> None:
    from shape.privacy.redact import CaptureConfig, redact_profile

    values = [f"bad{i:02d}" for i in range(30) for _ in range(10)]
    table = pa.table({"code": values})
    profile = shape.profile(table)
    result = shape.check(profile, contract("good"), data={"batch": table} if exact else None)
    assert len(result.violations[0]["observed"]["non_matching_values"]) == 20
    sensitive = redact_profile(profile, CaptureConfig(classifications={"code": "CONFIDENTIAL"}))
    result = shape.check(sensitive, contract("good"), data={"batch": table} if exact else None)
    if exact:
        assert result.violations[0]["observed"]["non_matching_values"] == []
    else:
        assert result.not_evaluable and not result.violations
    assert "bad00" not in json.dumps(result.to_dict())


def test_folded_profile_stays_blind_and_data_overrides() -> None:
    from shape.privacy.redact import redact_profile

    table = pa.table({"code": ["123"] * 99 + ["bad"]})
    profile = redact_profile(shape.profile(table))
    result = shape.check(profile, contract(r"\d{3}"))
    assert result.not_evaluable and not result.passed
    result = shape.check(profile, contract(r"\d{3}"), data={"batch": table})
    assert not result.passed and not result.not_evaluable
    assert result.violations[0]["observed"]["non_matching_values"] == []


@pytest.mark.parametrize("strength", ["soft", "learned"])
def test_rule_local_strength_and_enforcement(strength: str) -> None:
    table = pa.table({"code": ["bad"]})
    profile = shape.profile(table)
    rules = contract({"expression": r"\d{3}", "strength": strength})
    assert shape.check(profile, rules).passed
    assert shape.check(profile, rules).warnings[0]["strength"] == strength
    assert not shape.check(profile, rules, strict=True).passed
    assert shape.check(profile, rules, enforce_learned=True).passed is (strength == "soft")


def test_exact_data_replaces_definite_profile_violation() -> None:
    profile = shape.profile(pa.table({"code": ["bad"]}))
    assert not shape.check(profile, contract(r"\d{3}")).passed
    assert shape.check(
        profile, contract(r"\d{3}"), data={"batch": pa.table({"code": ["123"]})}
    ).passed
