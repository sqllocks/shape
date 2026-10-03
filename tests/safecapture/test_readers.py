"""W1-11 deliverable 6: every reader works on a safe capture, and says what it cannot evaluate."""

from __future__ import annotations

import csv
import json
import re
import warnings
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main

MESSAGE = (
    "not evaluable: {column} was captured safe (statistics and formats only); "
    "re-profile with --capture full"
)


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory, profile: Any) -> dict[str, Path]:
    d = tmp_path_factory.mktemp("readers")
    out = {
        "full": d / "full.shape",
        "safe": d / "safe.shape",
        "classified": d / "classified.shape",
    }
    shape.save(profile, str(out["full"]), capture="full")
    shape.save(profile, str(out["safe"]))
    shape.save(profile, str(out["classified"]), classifications={"city": "CONFIDENTIAL"})
    return out


def load(path: Path) -> Any:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return shape.load(str(path))


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


# --- shape diff ----------------------------------------------------------------------------------


def kinds(result: Any, column: str) -> set[str]:
    return {n["kind"] for n in result.not_evaluable if n["column"] == column}


def test_the_same_data_full_against_safe_is_not_drift(files: dict[str, Path]) -> None:
    result = shape.diff(load(files["full"]), load(files["safe"]))
    assert result.drifted is False and result.changes == []
    # what could not be compared is listed, and never counted as drift
    assert "range_change" in kinds(result, "amount")
    assert "category_shift" in kinds(result, "grade")
    assert result.to_dict()["not_evaluable"] == result.not_evaluable


def test_a_fit_that_names_the_minimum_is_not_a_distribution_change(tmp_path: Path) -> None:
    import random

    random.seed(11)
    p = shape.profile(pa.table({"u": [random.uniform(10, 20) for _ in range(500)]}))
    assert p.tables["table"]["columns"]["u"]["distribution"] == "uniform"
    shape.save(p, str(tmp_path / "full.shape"), capture="full")
    shape.save(p, str(tmp_path / "safe.shape"))
    result = shape.diff(load(tmp_path / "full.shape"), load(tmp_path / "safe.shape"))
    assert result.drifted is False
    assert "distribution_change" in kinds(result, "u")


def test_the_other_direction_is_the_same(files: dict[str, Path]) -> None:
    result = shape.diff(load(files["safe"]), load(files["full"]))
    assert result.drifted is False
    assert "range_change" in kinds(result, "amount")


def test_two_full_captures_list_nothing_not_evaluable(files: dict[str, Path]) -> None:
    result = shape.diff(load(files["full"]), load(files["full"]))
    assert result.not_evaluable == [] and "not_evaluable" not in result.to_dict()


def test_a_record_names_the_column_the_kind_and_the_side(files: dict[str, Path]) -> None:
    result = shape.diff(load(files["full"]), load(files["safe"]))
    (rec,) = [
        n for n in result.not_evaluable if n["column"] == "amount" and n["kind"] == "range_change"
    ]
    assert rec["captured_safe"] == ["current"]
    assert "captured safe" in rec["reason"]
    both = shape.diff(load(files["safe"]), load(files["safe"]))
    (rec2,) = [
        n for n in both.not_evaluable if n["column"] == "amount" and n["kind"] == "range_change"
    ]
    assert rec2["captured_safe"] == ["baseline", "current"]


def test_two_safe_captures_compare_what_both_hold(files: dict[str, Path]) -> None:
    result = shape.diff(load(files["safe"]), load(files["safe"]))
    assert result.drifted is False
    assert "category_shift" not in kinds(result, "grade")  # both folded alike: compared


def _changed_table(profile_table: pa.Table) -> pa.Table:
    cols = {n: profile_table[n].to_pylist() for n in profile_table.column_names}
    cols["age"] = [a + 25 for a in cols["age"]]  # a real, large shift in a column that is public
    cols["amount"] = [a * 4 for a in cols["amount"]]  # and in a sensitive one
    return pa.table(cols)


def test_a_real_change_is_still_found_next_to_what_cannot_be_evaluated(
    table: pa.Table, files: dict[str, Path], tmp_path: Path
) -> None:
    moved = shape.profile(_changed_table(table))
    shape.save(moved, str(tmp_path / "moved.shape"))
    result = shape.diff(load(files["safe"]), load(tmp_path / "moved.shape"))
    found = {(c["column"], c["kind"]) for c in result.changes}
    assert ("age", "mean_shift") in found  # public column
    assert ("amount", "spread_change") in found  # the spread is a statistic and is kept
    assert result.drifted is True
    assert "range_change" in kinds(result, "amount")


def test_a_full_baseline_against_a_safe_current_still_finds_the_shift(
    table: pa.Table, files: dict[str, Path], tmp_path: Path
) -> None:
    moved = shape.profile(_changed_table(table))
    shape.save(moved, str(tmp_path / "moved.shape"))
    result = shape.diff(load(files["full"]), load(tmp_path / "moved.shape"))
    assert ("age", "mean_shift") in {(c["column"], c["kind"]) for c in result.changes}


def test_cli_diff_lists_not_evaluable_and_does_not_fail_on_it(
    files: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "diff", str(files["full"]), str(files["safe"]), "--fail-on-drift")
    assert code == 0
    doc = json.loads(out)
    assert doc["drifted"] is False and doc["not_evaluable"]


def test_a_dataset_names_the_table_in_not_evaluable(tmp_path: Path) -> None:
    a = pa.table({"k": list(range(300)), "v": [float(i) * 1.5 for i in range(300)]})
    p = shape.profile({"a": a})
    shape.save(p, str(tmp_path / "full.shape"), capture="full")
    shape.save(p, str(tmp_path / "safe.shape"))
    result = shape.diff(load(tmp_path / "full.shape"), load(tmp_path / "safe.shape"))
    assert result.drifted is False
    assert {n["column"] for n in result.not_evaluable} <= {"a.k", "a.v"}
    assert result.not_evaluable


def test_a_placeholder_that_only_one_side_can_see_is_not_a_surge(tmp_path: Path) -> None:
    vals = [-999] * 30 + [i % 40 for i in range(370)]
    p = shape.profile(pa.table({"d": vals, "g": ["x", "y"] * 200}))
    shape.save(p, str(tmp_path / "full.shape"), capture="full")
    shape.save(p, str(tmp_path / "safe.shape"), classifications={"d": "CONFIDENTIAL"})
    result = shape.diff(load(tmp_path / "safe.shape"), load(tmp_path / "full.shape"))
    assert "placeholder_surge" not in {c["kind"] for c in result.changes}
    assert "placeholder_surge" in kinds(result, "d")


# --- shape check ---------------------------------------------------------------------------------


def contract(tmp_path: Path, rules: dict[str, Any], name: str = "c.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"columns": rules}))
    return path


def test_an_enum_rule_on_a_sensitive_column_is_not_evaluable(
    files: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = contract(tmp_path, {"city": {"allowed_values": ["Paris", "Rome", "Oslo", "Zzyzx"]}})
    code, out, err = run(capsys, "check", str(files["classified"]), str(c))
    assert code == 2
    assert MESSAGE.format(column="city") in err
    doc = json.loads(out)
    assert doc["passed"] is False and doc["violations"] == []
    assert doc["not_evaluable"] == [
        {
            "column": "city",
            "rule": "allowed_values",
            "reason": "city was captured safe (statistics and formats only); "
            "re-profile with --capture full",
        }
    ]


def test_the_same_rule_on_a_full_capture_is_evaluated(
    files: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = contract(tmp_path, {"city": {"allowed_values": ["Paris", "Rome", "Oslo", "Zzyzx"]}})
    code, out, err = run(capsys, "check", str(files["full"]), str(c))
    assert code == 0 and "not evaluable" not in err
    assert json.loads(out) == {"passed": True, "violations": []}


def test_a_violation_is_reported_with_exit_1_even_when_something_else_is_not_evaluable(
    files: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    c = contract(
        tmp_path,
        {
            "city": {"allowed_values": ["Paris", "Rome", "Oslo", "Zzyzx"]},
            "ssn": {"max_null_rate": -1},
        },
    )
    code, out, err = run(capsys, "check", str(files["classified"]), str(c))
    doc = json.loads(out)
    assert code == 1
    assert doc["violations"] and doc["not_evaluable"]
    assert MESSAGE.format(column="city") in err


@pytest.mark.parametrize(
    ("column", "rules"),
    [
        ("amount", {"min": 0}),
        ("amount", {"max": 10}),
        ("id", {"distribution": "normal"}),
        ("amount", {"no_placeholder": True}),
    ],
)
def test_each_rule_that_needs_suppressed_evidence_is_not_evaluable(
    files: dict[str, Path], column: str, rules: dict[str, Any]
) -> None:
    result = shape.check(load(files["safe"]), {"columns": {column: rules}})
    assert result.passed is False and result.violations == []
    assert [n["column"] for n in result.not_evaluable] == [column]
    assert result.not_evaluable[0]["rule"] == next(iter(rules))


@pytest.mark.parametrize("rules", [{"dtype": "float"}, {"nullable": False}, {"max_null_rate": 0.1}])
def test_rules_on_kept_statistics_are_evaluated_on_a_safe_capture(
    files: dict[str, Path], rules: dict[str, Any]
) -> None:
    result = shape.check(load(files["safe"]), {"columns": {"amount": rules}})
    assert result.passed is True and result.not_evaluable == []


def test_a_numeric_bound_on_a_non_sensitive_column_that_keeps_its_extremes_is_evaluated(
    files: dict[str, Path],
) -> None:
    ok = shape.check(load(files["safe"]), {"columns": {"age": {"min": 0, "max": 100}}})
    assert ok.passed is True and ok.not_evaluable == []
    bad = shape.check(load(files["safe"]), {"columns": {"age": {"max": 30}}})
    assert bad.passed is False and bad.violations and bad.not_evaluable == []


def test_an_enum_rule_that_a_released_value_breaks_is_a_violation_not_a_gap(
    files: dict[str, Path],
) -> None:
    result = shape.check(load(files["safe"]), {"columns": {"grade": {"allowed_values": ["A"]}}})
    assert result.passed is False
    assert result.violations and result.violations[0]["rule"] == "allowed_values"


def test_an_enum_rule_on_folded_categories_cannot_be_called_a_pass(files: dict[str, Path]) -> None:
    allowed = ["A", "B", "edge5", "edge4", "edge1"]
    result = shape.check(load(files["safe"]), {"columns": {"grade": {"allowed_values": allowed}}})
    assert result.violations == []
    assert [n["rule"] for n in result.not_evaluable] == ["allowed_values"]
    assert result.passed is False


def test_a_dataset_contract_qualifies_the_column(tmp_path: Path) -> None:
    a = pa.table({"k": list(range(300)), "v": [float(i) * 1.5 for i in range(300)]})
    shape.save(shape.profile({"a": a}), str(tmp_path / "s.shape"))
    result = shape.check(
        load(tmp_path / "s.shape"), {"tables": {"a": {"columns": {"v": {"min": 0}}}}}
    )
    assert [n["column"] for n in result.not_evaluable] == ["a.v"]


def test_check_result_dict_has_the_key_only_when_something_is_not_evaluable(
    files: dict[str, Path],
) -> None:
    ok = shape.check(load(files["full"]), {"columns": {"age": {"min": 0}}})
    assert ok.to_dict() == {"passed": True, "violations": []}  # the 12.2 shape, unchanged


# --- shape plan and shape generate --from --------------------------------------------------------


def plan_items(path: Path) -> dict[str, dict[str, str]]:
    from shape.generation.fit import fit_schema

    items = fit_schema(load(path), rows=100).plan.to_dict()["items"]
    return {i["evidence"]: i for i in items}


def test_a_sensitive_column_is_approximate_in_the_plan(files: dict[str, Path]) -> None:
    items = plan_items(files["safe"])
    for column in ("email", "ssn", "card", "amount", "note"):
        item = items[f"table.{column}.min_value"]
        assert item["status"] == "approximate", column
        assert "captured safe" in item["reason"] and "pattern" in item["reason"]
    assert items["table.email.value_counts_ext"]["status"] == "approximate"


def test_a_full_capture_plan_has_no_captured_safe_item(files: dict[str, Path]) -> None:
    assert not [i for i in plan_items(files["full"]).values() if "captured safe" in i["reason"]]


def test_folded_categories_are_approximate_in_the_plan(files: dict[str, Path]) -> None:
    items = plan_items(files["safe"])
    assert items["table.grade.enum_values"]["status"] == "approximate"
    assert "captured safe" in items["table.grade.enum_values"]["reason"]


def test_the_plan_command_marks_them_too(
    files: dict[str, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "plan", str(files["safe"]))
    assert code == 0
    items = json.loads(out)["items"]
    assert any(i["status"] == "approximate" and "captured safe" in i["reason"] for i in items)


def test_generate_from_a_safe_capture_makes_sensitive_columns_from_their_pattern(
    files: dict[str, Path],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    planted: dict[str, str],
) -> None:
    out_dir = tmp_path / "out"
    code, _, err = run(
        capsys, "generate", "--from", str(files["safe"]), "--rows", "300", "--format", "csv",
        "-o", str(out_dir), "--seed", "3",
    )  # fmt: skip
    assert code == 0, err
    (path,) = out_dir.glob("*.csv")
    rows = list(csv.DictReader(path.open()))
    assert len(rows) == 300
    assert all(re.fullmatch(r"\d{3}-\d{2}-\d{4}", r["ssn"]) for r in rows)
    assert all("@" in r["email"] for r in rows)
    text = path.read_text()
    assert "__OTHER__" not in text
    for value in (planted["email"], planted["ssn"], planted["rare_city"], planted["extreme"]):
        assert value not in text
    assert {r["grade"] for r in rows} <= {"A", "B", "edge5"}  # only released categories


def test_a_generate_from_a_full_capture_is_unchanged(
    files: dict[str, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_dir = tmp_path / "out"
    code, _, err = run(
        capsys, "generate", "--from", str(files["full"]), "--rows", "100", "--format", "csv",
        "-o", str(out_dir),
    )  # fmt: skip
    assert code == 0, err
    assert "captured safe" not in err
