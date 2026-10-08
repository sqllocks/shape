"""W7-03 items 5 and 6: contract rule strength (hard, soft, learned), ``shape check --strict`` and
``--enforce-learned``, ``strength`` and ``warnings`` in the result, and the contract reader."""

from __future__ import annotations

import copy
import json
import warnings
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.contracts.v1 import STRENGTHS, ContractError

ROOT = Path(__file__).parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
OLD_PROFILE = FIXTURES / "profiles" / "pre_w3_07.shape"


@pytest.fixture(scope="module")
def prof() -> Any:
    rng = np.random.default_rng(2)
    n = 400
    city = rng.integers(0, 10, n)
    return shape.profile(
        pa.table(
            {
                "id": pa.array(np.arange(n)),
                "age": pa.array(rng.integers(20, 60, n)),
                "city": pa.array([f"c{c}" for c in city]),
                "zip": pa.array([f"z{(c * 7 + int(rng.integers(0, 3))) % 31}" for c in city]),
            }
        )
    )


def codes(result: Any) -> list[tuple[Any, str, str]]:
    return [(v["column"], v["rule"], v.get("strength")) for v in result.violations]


# --- compatibility: no strength key, the result of before the change ---------------------------


def _fixture_files() -> list[Path]:
    return sorted(
        [
            *(FIXTURES / "contracts").glob("*.json"),
            *(ROOT / "demo" / "contracts").glob("*.json"),
            *(ROOT / "docs" / "bridge" / "vectors" / "fixtures").glob("contract*.json"),
        ]
    )


@pytest.mark.parametrize("path", _fixture_files(), ids=lambda p: f"{p.parent.name}/{p.stem}")
def test_a_contract_without_strength_gives_the_result_it_always_did(path: Path) -> None:
    golden = FIXTURES / "contracts" / "golden" / f"{path.parent.name}__{path.stem}.txt"
    assert golden.exists(), f"no golden result for {path}"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old = shape.load(OLD_PROFILE)
    try:
        text = json.dumps(shape.check(old, path).to_dict(), indent=2)
    except Exception as exc:  # noqa: BLE001 - an error is part of the golden output
        text = f"{type(exc).__name__}: {exc}"
    assert text + "\n" == golden.read_text(encoding="utf-8")


def test_every_contract_fixture_has_a_golden_file() -> None:
    names = {f"{p.parent.name}__{p.stem}.txt" for p in _fixture_files()}
    have = {p.name for p in (FIXTURES / "contracts" / "golden").glob("*.txt")}
    assert names == have


def test_the_exit_code_and_json_of_a_contract_without_strength_are_unchanged(
    tmp_path: Path, prof: Any
) -> None:
    shape.save(prof, tmp_path / "p.shape")
    res = tmp_path / "r.json"
    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"columns": {"age": {"min": 30}}}))
    assert main(["check", str(tmp_path / "p.shape"), str(contract), "--json", str(res)]) == 1
    out = json.loads(res.read_text())
    assert set(out) == {"passed", "violations"}
    assert all("strength" not in v for v in out["violations"])


# --- soft, hard, learned -----------------------------------------------------------------------

BROKEN = {"age": {"min": 30}}  # the youngest are 20


def test_a_soft_rule_passes_where_the_hard_one_fails_and_fails_with_strict(prof: Any) -> None:
    hard = shape.check(prof, {"columns": BROKEN})
    assert hard.passed is False and codes(hard) == [("age", "min", None)]
    soft = shape.check(prof, {"columns": {"age": {"min": 30, "strength": "soft"}}})
    assert soft.passed is True and soft.violations == []
    assert [(w["column"], w["rule"], w["strength"]) for w in soft.warnings] == [
        ("age", "min", "soft")
    ]
    strict = shape.check(prof, {"columns": {"age": {"min": 30, "strength": "soft"}}}, strict=True)
    assert strict.passed is False
    assert codes(strict) == [("age", "min", "soft")] and strict.warnings == []


def test_an_explicit_hard_is_the_default() -> None:
    assert STRENGTHS == ("hard", "soft", "learned")


def test_hard_with_a_declared_strength_fails_and_carries_the_key(prof: Any) -> None:
    r = shape.check(prof, {"columns": {"age": {"min": 30, "strength": "hard"}}})
    assert r.passed is False and codes(r) == [("age", "min", "hard")] and r.warnings == []
    assert list(r.to_dict()) == ["passed", "violations", "warnings"]


def test_learned_is_a_warning_until_it_is_enforced(prof: Any) -> None:
    contract = {"columns": {"age": {"min": 30, "strength": "learned"}}}
    default = shape.check(prof, contract)
    assert default.passed is True and [w["strength"] for w in default.warnings] == ["learned"]
    enforced = shape.check(prof, contract, enforce_learned=True)
    assert enforced.passed is False and codes(enforced) == [("age", "min", "learned")]
    soft = {"columns": {"age": {"min": 30, "strength": "soft"}}}
    assert shape.check(prof, soft, enforce_learned=True).passed is True  # only learned is enforced


def test_the_object_form_sets_a_strength_per_rule(prof: Any) -> None:
    contract = {
        "columns": {"age": {"min": 30, "max": 25, "strength": {"min": "soft", "max": "hard"}}}
    }
    r = shape.check(prof, contract)
    assert r.passed is False
    assert codes(r) == [("age", "max", "hard")]  # a rule the object does not name is hard
    assert [(w["rule"], w["strength"]) for w in r.warnings] == [("min", "soft")]
    unnamed = {"columns": {"age": {"min": 30, "max": 25, "strength": {"min": "soft"}}}}
    assert codes(shape.check(prof, unnamed)) == [("age", "max", "hard")]


def test_a_true_rate_violation_takes_the_harder_bound(prof: Any) -> None:
    c: dict[str, Any] = {
        "columns": {
            "age": {
                "min_true_rate": 0.5,
                "max_true_rate": 0.9,
                "strength": {"min_true_rate": "soft", "max_true_rate": "hard"},
            }
        }
    }
    r = shape.check(prof, c)  # age is not boolean: one true_rate violation
    assert [v["rule"] for v in r.violations + r.warnings] == ["true_rate"]
    assert r.passed is False


def test_a_missing_column_takes_the_columns_one_word_strength(prof: Any) -> None:
    soft = shape.check(prof, {"columns": {"ghost": {"dtype": "integer", "strength": "soft"}}})
    assert soft.passed is True and soft.warnings[0]["rule"] == "column_exists"
    obj = shape.check(
        prof, {"columns": {"ghost": {"dtype": "integer", "strength": {"dtype": "soft"}}}}
    )
    assert obj.passed is False  # an object names rules, and "column_exists" is not one


def test_row_count_strength(prof: Any) -> None:
    c = {"row_count": {"min": 1000, "strength": "soft"}}
    r = shape.check(prof, c)
    assert r.passed is True and r.warnings[0]["rule"] == "row_count.min"
    assert r.warnings[0]["strength"] == "soft"
    assert shape.check(prof, c, strict=True).passed is False
    assert shape.check(prof, {"row_count": {"min": 1000}}).passed is False


def test_fd_implies_and_reference_pair_entries(prof: Any) -> None:
    fd = {"determinant": "zip", "dependent": "city", "min_confidence": 1.0}
    implies = {
        "if": {"column": "city", "equals": "c1"},
        "then": {"column": "zip", "equals": "z0"},
        "min_confidence": 1.0,
    }
    ref = {"columns": ["city", "zip"], "reference": "nowhere", "min_match_rate": 0.99}
    for key, rule in (("fd", fd), ("implies", implies), ("reference_pair", ref)):
        hard = shape.check(prof, {key: [rule]})
        assert hard.passed is False, key
        soft = shape.check(prof, {key: [{**rule, "strength": "soft"}]})
        assert soft.passed is True and soft.violations == [], key
        assert soft.warnings and all(w["strength"] == "soft" for w in soft.warnings), key
        assert shape.check(prof, {key: [{**rule, "strength": "soft"}]}, strict=True).passed is False
    mixed = shape.check(prof, {"fd": [fd, {**fd, "dependent": "age", "strength": "soft"}]})
    assert [v["strength"] for v in mixed.violations] == ["hard"]
    assert [w["strength"] for w in mixed.warnings] == ["soft"]


def test_one_declared_strength_puts_it_on_every_violation(prof: Any) -> None:
    c = {
        "row_count": {"min": 1000},
        "columns": {"age": {"min": 30, "strength": "soft"}, "id": {"max": 5}},
        "required_columns": ["nope"],
    }
    r = shape.check(prof, c)
    every = r.violations + r.warnings
    assert len(every) == 4 and all(v["strength"] in STRENGTHS for v in every)
    assert {v["rule"]: v["strength"] for v in every} == {
        "row_count.min": "hard",
        "min": "soft",
        "max": "hard",
        "required_column": "hard",
    }


def test_a_dataset_contract_with_strength() -> None:
    a = pa.table({"x": pa.array(np.arange(100))})
    b = pa.table({"y": pa.array(np.arange(50))})
    ds = shape.profile({"a": a, "b": b})
    c = {
        "tables": {
            "a": {"columns": {"x": {"min": 10, "strength": "soft"}}},
            "b": {"columns": {"y": {"min": 10}}},
            "c": {"row_count": {"min": 1}},
        }
    }
    r = shape.check(ds, c)
    assert r.passed is False
    assert {(v["column"], v["strength"]) for v in r.violations} == {("b.y", "hard"), (None, "hard")}
    assert [(w["column"], w["strength"]) for w in r.warnings] == [("a.x", "soft")]
    assert shape.check(ds, {"tables": {"a": c["tables"]["a"]}}).passed is True


# --- the reader --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("contract", "needle"),
    [
        ({"columns": {"age": {"min": 1, "strength": "firm"}}}, "age"),
        ({"columns": {"age": {"min": 1, "strength": 3}}}, "age"),
        ({"columns": {"age": {"min": 1, "strength": {"min": "firm"}}}}, "'min'"),
        ({"columns": {"age": {"min": 1, "strength": {"bogus": "soft"}}}}, "bogus"),
        ({"columns": {"age": {"min": 1, "strength": {"strength": "soft"}}}}, "age"),
        ({"columns": {"age": {"min": 1, "strength": ["soft"]}}}, "age"),
        ({"row_count": {"min": 1, "strength": "x"}}, "row_count"),
        (
            {"fd": [{"determinant": "a", "dependent": "b", "min_confidence": 1, "strength": "x"}]},
            "'a'",
        ),
        (
            {
                "implies": [
                    {
                        "if": {"column": "a", "equals": 1},
                        "then": {"column": "b", "equals": 2},
                        "min_confidence": 1,
                        "strength": "x",
                    }
                ]
            },
            "implies",
        ),
        (
            {
                "reference_pair": [
                    {"columns": ["a"], "reference": "r", "min_match_rate": 1, "strength": "x"}
                ]
            },
            "reference_pair",
        ),
    ],
)
def test_an_unknown_strength_is_a_contract_error_naming_the_rule(
    prof: Any, contract: dict[str, Any], needle: str
) -> None:
    with pytest.raises(ContractError, match=needle):
        shape.check(prof, contract)


@pytest.mark.parametrize(
    "contract",
    [
        {"strength": "soft"},  # not a contract key
        {"columns": {"a": {"no_placeholder": {"max_share": 0.1, "strength": "soft"}}}},
        {"row_count": {"min": 1, "strength": {"min": "soft"}}},  # a word only
        {
            "implies": [
                {
                    "if": {"column": "a", "equals": 1, "strength": "soft"},
                    "then": {"column": "b", "equals": 2},
                    "min_confidence": 1,
                }
            ]
        },
        {"max_implausible_rate": {"strength": "soft"}},
        {"allow_extra_columns": "soft", "required_columns": [{"strength": "soft"}]},
    ],
)
def test_strength_is_rejected_where_it_is_not_allowed(prof: Any, contract: dict[str, Any]) -> None:
    with pytest.raises(ContractError):
        shape.check(prof, contract)


def test_a_dataset_contract_validates_the_strength_of_each_table() -> None:
    ds = shape.profile(
        {"a": pa.table({"x": pa.array([1, 2])}), "b": pa.table({"y": pa.array([1])})}
    )
    bad = {"tables": {"a": {"columns": {"x": {"min": 1, "strength": "nope"}}}}}
    with pytest.raises(ContractError, match="x"):
        shape.check(ds, bad)


# --- the command ------------------------------------------------------------------------------


def _run(tmp_path: Path, prof: Any, contract: dict[str, Any], *flags: str) -> tuple[int, Any]:
    shape.save(prof, tmp_path / "p.shape")
    (tmp_path / "c.json").write_text(json.dumps(contract))
    res = tmp_path / "r.json"
    if res.exists():
        res.unlink()
    code = main(
        ["check", str(tmp_path / "p.shape"), str(tmp_path / "c.json"), "--json", str(res), *flags]
    )
    return code, (json.loads(res.read_text()) if res.exists() else None)


def test_the_exit_codes(tmp_path: Path, prof: Any, capsys: pytest.CaptureFixture[str]) -> None:
    soft = {"columns": {"age": {"min": 30, "strength": "soft"}}}
    learned = {"columns": {"age": {"min": 30, "strength": "learned"}}}
    code, out = _run(tmp_path, prof, soft)
    assert code == 0 and out["passed"] is True and out["violations"] == []
    assert out["warnings"][0]["strength"] == "soft"
    assert _run(tmp_path, prof, soft, "--strict")[0] == 1
    assert _run(tmp_path, prof, learned)[0] == 0
    assert _run(tmp_path, prof, learned, "--enforce-learned")[0] == 1
    assert _run(tmp_path, prof, soft, "--enforce-learned")[0] == 0
    assert _run(tmp_path, prof, {"columns": {"age": {"min": 30, "strength": "hard"}}})[0] == 1
    capsys.readouterr()
    assert _run(tmp_path, prof, {"columns": {"age": {"min": 30, "strength": "firm"}}})[0] == 2
    assert "age" in capsys.readouterr().err


def test_a_contract_read_from_a_file_with_the_old_profile(prof: Any) -> None:
    c = copy.deepcopy({"columns": {"visits": {"min": 5, "strength": "soft"}}})
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        old = shape.load(OLD_PROFILE)
    r = shape.check(old, c)
    assert r.passed is True and r.warnings and r.warnings[0]["strength"] == "soft"
