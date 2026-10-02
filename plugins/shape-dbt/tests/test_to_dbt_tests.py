"""`shape to-dbt-tests`: a contract (v1) or a profile as dbt tests, and back."""

from __future__ import annotations

import json

import pyarrow as pa
import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st
from shape_dbt.project import DbtProjectError
from shape_dbt.totests import (
    NOT_EXPRESSIBLE,
    bounds_from_profile,
    compile_tests,
    contract_from_dbt_tests,
    contract_from_profile,
    contract_from_schema_yaml,
    expressible,
    merge_schema_docs,
    normalize_contract,
    not_expressible,
    packages_yaml,
)

import shape

CONTRACT = {
    "row_count": {"min": 1000, "max": 5000000},
    "columns": {
        "customer_id": {"dtype": "integer", "nullable": False, "unique": True},
        "email": {"max_null_rate": 0.05, "pattern": "email"},
        "status": {"allowed_values": ["placed", "shipped", "returned"]},
        "amount": {"min": 0, "max": 100000, "distribution": "log_normal"},
    },
    "required_columns": ["customer_id", "order_date"],
    "allow_extra_columns": False,
}


def test_each_rule_becomes_its_test():
    doc = compile_tests(CONTRACT, model="orders").doc
    (entry,) = doc["models"]
    assert doc["version"] == 2 and entry["name"] == "orders"
    names = [next(iter(t)) for t in entry["data_tests"]]
    assert names == [
        "dbt_expectations.expect_table_row_count_to_be_between",
        "dbt_expectations.expect_table_columns_to_match_set",
    ]
    cols = {c["name"]: c for c in entry["columns"]}
    tests = {n: [next(iter(t)) for t in c["data_tests"]] for n, c in cols.items() if "data_tests" in c}
    assert tests["customer_id"] == ["not_null", "unique", "dbt_expectations.expect_column_to_exist"]
    assert tests["email"] == ["dbt_utils.not_null_proportion"]
    assert tests["status"] == ["accepted_values"]
    assert tests["amount"] == ["dbt_utils.accepted_range"]
    assert tests["order_date"] == ["dbt_expectations.expect_column_to_exist"]
    args = {k: v for t in cols["amount"]["data_tests"] for k, v in t.items()}
    assert args["dbt_utils.accepted_range"]["arguments"] == {
        "inclusive": True,
        "min_value": 0,
        "max_value": 100000,
    }
    email = cols["email"]["data_tests"][0]["dbt_utils.not_null_proportion"]
    assert email["arguments"] == {"at_least": 0.95}
    status = cols["status"]["data_tests"][0]["accepted_values"]["arguments"]
    assert status == {"values": ["placed", "shipped", "returned"], "quote": True}
    # every test carries the tag
    assert all(
        t[next(iter(t))]["config"] == {"tags": ["shape"]}
        for c in entry["columns"]
        for t in c.get("data_tests", [])
    )


def test_numeric_allowed_values_are_not_quoted():
    doc = compile_tests({"columns": {"n": {"allowed_values": [1, 2, 3]}}}, model="m").doc
    test = doc["models"][0]["columns"][0]["data_tests"][0]["accepted_values"]["arguments"]
    assert test == {"values": [1, 2, 3], "quote": False}


def test_what_dbt_cannot_state_is_kept_as_meta_and_reported():
    compiled = compile_tests(CONTRACT, model="orders")
    cols = {c["name"]: c for c in compiled.doc["models"][0]["columns"]}
    assert cols["email"]["meta"] == {"shape": {"pattern": "email"}}
    assert cols["amount"]["meta"] == {"shape": {"distribution": "log_normal"}}
    assert cols["customer_id"]["meta"] == {"shape": {"dtype": "integer"}}
    assert set(not_expressible(CONTRACT)) == {
        "customer_id.dtype",
        "email.pattern",
        "amount.distribution",
    }
    assert any("email.pattern" in n for n in compiled.notes)


def test_the_packages_are_declared():
    assert compile_tests(CONTRACT, model="o").packages == {"dbt_utils", "dbt_expectations"}
    assert compile_tests({"columns": {"a": {"nullable": False}}}, model="o").packages == set()
    assert compile_tests({"columns": {"a": {"min": 1}}}, model="o").packages == {"dbt_utils"}
    both = yaml.safe_load(packages_yaml({"dbt_utils", "dbt_expectations"}))["packages"]
    assert [p["package"] for p in both] == ["dbt-labs/dbt_utils", "metaplane/dbt_expectations"]
    assert "Needs these dbt packages" in compile_tests(CONTRACT, model="o").yaml()


def test_the_round_trip_with_meta_gives_the_contract_back():
    compiled = compile_tests(CONTRACT, model="orders")
    assert contract_from_dbt_tests(compiled.yaml()) == normalize_contract(CONTRACT)


def test_the_round_trip_from_the_tests_alone_gives_what_dbt_can_express():
    compiled = compile_tests(CONTRACT, model="orders")
    back = contract_from_dbt_tests(compiled.yaml(), use_meta=False)
    assert back == expressible(CONTRACT)
    expected = {
        "row_count": {"min": 1000, "max": 5000000},
        "columns": {
            "customer_id": {"nullable": False, "unique": True},
            "email": {"max_null_rate": 0.05},
            "status": {"allowed_values": ["placed", "shipped", "returned"]},
            "amount": {"min": 0, "max": 100000},
        },
        "required_columns": ["customer_id", "order_date"],
        "allow_extra_columns": False,
    }
    assert back == expected
    # and what is lost is exactly what NOT_EXPRESSIBLE names
    lost = {
        k for rules in CONTRACT["columns"].values() for k in rules
    } - {k for rules in back["columns"].values() for k in rules}
    assert lost == {"dtype", "pattern", "distribution"}
    assert lost <= set(NOT_EXPRESSIBLE)


@pytest.mark.parametrize("kind", ["models", "seeds", "sources"])
@pytest.mark.parametrize("args_style", ["arguments", "inline"])
@pytest.mark.parametrize("tests_key", ["data_tests", "tests"])
def test_the_round_trip_holds_for_every_layout(kind, args_style, tests_key):
    contract = {
        "tables": {
            "orders": CONTRACT,
            "customers": {"columns": {"id": {"nullable": False, "unique": True, "min": 1}}},
        }
    }
    compiled = compile_tests(
        contract, kind=kind, args_style=args_style, tests_key=tests_key, source_name="raw"
    )
    back, _ = contract_from_schema_yaml(compiled.yaml())
    assert back == normalize_contract(contract)["tables"]


def test_a_single_table_contract_needs_a_model_name():
    with pytest.raises(DbtProjectError, match="name of the dbt model"):
        compile_tests(CONTRACT)
    with pytest.raises(DbtProjectError, match="kind must be"):
        compile_tests(CONTRACT, model="o", kind="tables")
    with pytest.raises(DbtProjectError, match="args_style"):
        compile_tests(CONTRACT, model="o", args_style="x")


def test_text_bounds_are_not_compiled_and_are_kept_as_meta():
    contract = {"columns": {"code": {"min": "A", "max": "Z"}}}
    compiled = compile_tests(contract, model="m")
    col = compiled.doc["models"][0]["columns"][0]
    assert "data_tests" not in col and col["meta"] == {"shape": {"min": "A", "max": "Z"}}
    assert expressible(contract) == {}
    assert set(not_expressible(contract)) == {"code.min", "code.max"}


def test_rules_that_say_nothing_normalise_away():
    assert normalize_contract(
        {"columns": {"a": {"nullable": True, "unique": False}, "b": {}}, "allow_extra_columns": True}
    ) == {}


_names = st.sampled_from(["a", "b", "c", "d_1", "id"])
_numbers = st.one_of(st.integers(-10_000, 10_000), st.floats(-1e6, 1e6, allow_nan=False))
_rules = st.fixed_dictionaries(
    {},
    optional={
        "nullable": st.just(False),
        "unique": st.just(True),
        "allowed_values": st.lists(
            st.one_of(st.text("abcxyz", min_size=1, max_size=4), st.integers(0, 50)),
            min_size=1,
            max_size=4,
            unique=True,
        ),
        "min": _numbers,
        "max": _numbers,
        "max_null_rate": st.floats(0, 1).map(lambda x: round(x, 6)),
        "dtype": st.sampled_from(["integer", "float", "string", "boolean"]),
        "pattern": st.sampled_from(["email", "uuid"]),
        "distribution": st.sampled_from(["normal", "log_normal"]),
    },
)
_contracts = st.fixed_dictionaries(
    {},
    optional={
        "row_count": st.fixed_dictionaries({}, optional={"min": st.integers(0, 10**6), "max": st.integers(0, 10**9)}),
        "columns": st.dictionaries(_names, _rules, max_size=4),
        "required_columns": st.lists(_names, unique=True, max_size=3),
        "allow_extra_columns": st.booleans(),
    },
)


@settings(max_examples=150, deadline=None)
@given(_contracts)
def test_any_contract_round_trips(contract):
    """contract -> dbt tests -> contract: equal for what dbt can express, and equal for all of
    it when the meta is read too."""
    compiled = compile_tests(contract, model="m")
    text = compiled.yaml()
    assert contract_from_dbt_tests(text, model="m", use_meta=False) == expressible(contract)
    assert contract_from_dbt_tests(text, model="m", use_meta=True) == normalize_contract(contract)


def make_profile(rows: int = 400):
    import numpy as np

    rng = np.random.default_rng(5)
    table = pa.table(
        {
            "order_id": list(range(rows)),
            "amount": rng.lognormal(3, 0.4, rows),
            "status": rng.choice(["placed", "shipped", "returned"], rows),
            "note": pa.array([None if i % 10 == 0 else f"n{i}" for i in range(rows)]),
        }
    )
    return table, shape.profile({"orders": table})


def test_a_contract_captured_from_a_profile_is_satisfied_by_it():
    _, profile = make_profile()
    contract = contract_from_profile(profile)
    rules = contract["tables"]["orders"]["columns"]
    assert rules["order_id"]["unique"] is True and rules["order_id"]["nullable"] is False
    assert rules["order_id"]["min"] == 0 and rules["order_id"]["max"] >= 399
    assert rules["status"]["allowed_values"] == ["placed", "returned", "shipped"]
    assert rules["amount"]["min"] < rules["amount"]["max"] and "unique" not in rules["amount"]
    assert 0.1 <= rules["note"]["max_null_rate"] <= 0.12
    assert shape.check(profile, contract).passed


def test_the_tests_of_a_profile_round_trip_and_still_pass_the_check():
    _, profile = make_profile()
    contract = contract_from_profile(profile)
    compiled = compile_tests(contract, kind="models")
    back, _ = contract_from_schema_yaml(compiled.yaml())
    assert {"tables": back} == normalize_contract(contract)
    assert shape.check(profile, {"tables": back}).passed


def test_distribution_bounds_follow_the_drift_defaults():
    table, profile = make_profile()
    bounds = bounds_from_profile(profile)["orders"]
    assert "order_id" not in bounds  # a key has no distribution worth bounding
    amount = table["amount"].to_pandas()
    lo, hi = bounds["amount"]["mean"]
    assert lo < amount.mean() < hi
    assert hi - lo == pytest.approx(2 * 0.5 * amount.std(ddof=1), rel=0.02)
    s_lo, s_hi = bounds["amount"]["std"]
    assert s_lo == pytest.approx(0.67 * amount.std(ddof=1), rel=0.02)
    assert s_hi == pytest.approx(1.5 * amount.std(ddof=1), rel=0.02)
    assert set(bounds["amount"]["quantiles"]) == {"0.25", "0.5", "0.75"}
    compiled = compile_tests(contract_from_profile(profile), bounds=bounds_from_profile(profile))
    _, read_back = contract_from_schema_yaml(compiled.yaml())
    assert read_back["orders"]["amount"] == bounds["amount"]


def test_a_single_table_profile_gives_a_single_table_contract():
    import pyarrow as pa

    profile = shape.profile(pa.table({"a": [1, 2, 3] * 20}), name="t")
    contract = contract_from_profile(profile)
    assert "tables" not in contract and contract["required_columns"] == ["a"]
    assert shape.check(profile, contract).passed
    assert bounds_from_profile(profile)["a"]["mean"][0] < 2 < bounds_from_profile(profile)["a"]["mean"][1]


def test_merging_adds_tests_to_an_existing_entry_without_repeating_any():
    base = yaml.safe_load(
        """
version: 2
models:
  - name: orders
    description: kept
    columns:
      - name: order_id
        data_type: bigint
        tests: [unique, not_null]
"""
    )
    contract = {"columns": {"order_id": {"nullable": False, "unique": True, "min": 0}}}
    doc = compile_tests(contract, model="orders").doc
    merged = merge_schema_docs(base, doc)
    (entry,) = merged["models"]
    col = entry["columns"][0]
    assert entry["description"] == "kept" and col["data_type"] == "bigint"
    assert "data_tests" not in col  # the file's own key (`tests`) is kept
    names = [t if isinstance(t, str) else next(iter(t)) for t in col["tests"]]
    assert names == ["unique", "not_null", "dbt_utils.accepted_range"]
    again = merge_schema_docs(merged, doc)
    assert again == merged
    assert base["models"][0]["columns"][0]["tests"] == ["unique", "not_null"]  # input untouched


def test_merging_adds_a_new_model_and_new_columns():
    base = {"version": 2, "models": [{"name": "a", "columns": [{"name": "x"}]}]}
    doc = compile_tests(
        {"tables": {"a": {"columns": {"y": {"nullable": False}}}, "b": {"columns": {"z": {"unique": True}}}}}
    ).doc
    merged = merge_schema_docs(base, doc)
    assert [m["name"] for m in merged["models"]] == ["a", "b"]
    assert [c["name"] for c in merged["models"][0]["columns"]] == ["x", "y"]


def test_the_cli_compiles_a_contract_and_writes_the_packages(tmp_path):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    src = tmp_path / "c.json"
    src.write_text(json.dumps(CONTRACT), encoding="utf-8")
    out, pk = tmp_path / "schema.yml", tmp_path / "packages.yml"
    code = cli.run_command(
        default_host(),
        "to-dbt-tests",
        [str(src), "-o", str(out), "--model", "orders", "--packages-out", str(pk)],
    )
    assert code == 0
    assert contract_from_dbt_tests(out.read_text(encoding="utf-8")) == normalize_contract(CONTRACT)
    assert {p["package"] for p in yaml.safe_load(pk.read_text(encoding="utf-8"))["packages"]} == {
        "dbt-labs/dbt_utils",
        "metaplane/dbt_expectations",
    }


def test_the_cli_compiles_a_profile_with_distribution_bounds(tmp_path):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    _, profile = make_profile()
    art = tmp_path / "orders.shape"
    shape.save(profile, art)
    out = tmp_path / "schema.yml"
    code = cli.run_command(
        default_host(), "to-dbt-tests", [str(art), "-o", str(out), "--distribution"]
    )
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "expect_column_mean_to_be_between" in text and "accepted_range" in text


def test_the_cli_refuses_bad_input_with_exit_2(tmp_path, capsys):
    from shape.plugins import cli
    from shape.plugins.host import default_host

    src = tmp_path / "c.json"
    src.write_text(json.dumps(CONTRACT), encoding="utf-8")
    out = str(tmp_path / "s.yml")
    host = default_host()
    assert cli.run_command(host, "to-dbt-tests", [str(src), "-o", out]) == 2  # no model
    assert cli.run_command(host, "to-dbt-tests", [str(src), "-o", out, "--model", "m", "--distribution"]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert cli.run_command(host, "to-dbt-tests", [str(bad), "-o", out, "--model", "m"]) == 2
    assert "not valid JSON" in capsys.readouterr().err
