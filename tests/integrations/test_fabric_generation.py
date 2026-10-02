"""PF-06: ``shape.integrations.fabric.generation`` and the ``generateSample`` helper.

Needs numpy, pyarrow, pandas and the ``sqllocks-shape-domains`` plugin only (no Fabric SDK,
Delta or Spark), so the ``pure-wheel`` job runs this file against the installed pure wheel with
``SHAPE_KERNEL=python``.
"""

from __future__ import annotations

import copy
import json

import pyarrow as pa
import pytest

import shape
from shape.integrations.fabric import generation, udf

DOMAIN = "retail"


@pytest.fixture(scope="module")
def retail():
    """Retail at small scale, seed 42, with its contract."""
    result = generation.generate_domain(DOMAIN, scale="small", seed=42)
    counts = {name: t.num_rows for name, t in result.tables.items()}
    return result, counts, generation.domain_contract(result.schema, counts)


# --------------------------------------------------------------------------- names


@pytest.mark.parametrize(
    "bad",
    ["", "../etc", "a b", "retail; DROP TABLE x", "a/b", "1abc", "x" * 65, "ré", None, 3, "a\nb"],
)
def test_check_name_rejects_anything_but_identifiers(bad):
    with pytest.raises(generation.GenerationRequestError):
        generation.check_name(bad, "domain")


@pytest.mark.parametrize("good", ["retail", "_t", "order_line", "A1", "x" * 64])
def test_check_name_accepts_identifiers(good):
    assert generation.check_name(good, "table") == good


def test_unknown_names_are_refused_before_anything_is_generated():
    with pytest.raises(generation.GenerationRequestError, match="no domain named 'nope'"):
        generation.generate_domain("nope")
    with pytest.raises(generation.GenerationRequestError, match="no scale preset 'huge'"):
        generation.generate_domain(DOMAIN, scale="huge")
    with pytest.raises(generation.GenerationRequestError, match="mode must be"):
        generation.generate_domain(DOMAIN, mode="snowflake")
    with pytest.raises(generation.GenerationRequestError, match="no table 'nope'"):
        generation.generate_domain(DOMAIN, row_counts={"nope": 5})
    with pytest.raises(generation.GenerationRequestError, match="whole number"):
        generation.generate_domain(DOMAIN, row_counts={"customer": -1})
    with pytest.raises(generation.GenerationRequestError):
        generation.plan_row_counts("../x")


# -------------------------------------------------------------------- generation


def test_generation_is_deterministic_and_the_seed_matters():
    a = generation.generate_domain(DOMAIN, scale="small", seed=7)
    b = generation.generate_domain(DOMAIN, scale="small", seed=7)
    c = generation.generate_domain(DOMAIN, scale="small", seed=8)
    for name in a.tables:
        assert a.tables[name].equals(b.tables[name])
    assert not a.tables["customer"].equals(c.tables["customer"])


def test_plan_row_counts_is_what_generation_produces(retail):
    result, counts, _ = retail
    assert generation.plan_row_counts(DOMAIN, "small") == counts
    star = generation.generate_domain(DOMAIN, scale="small", mode="star")
    assert generation.plan_row_counts(DOMAIN, "small", "star") == {
        name: t.num_rows for name, t in star.tables.items()
    }


def test_row_counts_override_single_tables():
    result = generation.generate_domain(DOMAIN, scale="small", row_counts={"customer": 37})
    assert result.tables["customer"].num_rows == 37
    assert result.verify_integrity() == []


# ---------------------------------------------------------------------- contract


@pytest.mark.parametrize("mode", ["3nf", "star"])
@pytest.mark.parametrize("seed", [1, 42, 1042])
def test_the_domain_contract_holds_for_what_the_domain_generates(mode, seed):
    result = generation.generate_domain(DOMAIN, scale="small", seed=seed, mode=mode)
    counts = {name: t.num_rows for name, t in result.tables.items()}
    contract = generation.contract_for_domain(DOMAIN, counts, mode)
    checked = shape.check(shape.profile(dict(result.tables), name=DOMAIN), contract)
    assert checked.passed, checked.violations


def test_the_contract_states_what_the_schema_says(retail):
    result, counts, contract = retail
    json.dumps(contract)  # plain JSON
    assert set(contract["tables"]) == set(result.schema.tables)
    customer = contract["tables"]["customer"]
    assert customer["row_count"] == {"min": counts["customer"], "max": counts["customer"]}
    assert customer["required_columns"] == list(result.schema.tables["customer"].columns)
    assert customer["allow_extra_columns"] is False
    rules = customer["columns"]
    assert rules["customer_id"] == {"dtype": "integer", "nullable": False, "unique": True}
    assert rules["loyalty_tier"]["allowed_values"] == ["Basic", "Silver", "Gold", "Platinum"]
    assert rules["is_active"]["dtype"] == "boolean"
    assert rules["signup_date"]["dtype"] == "datetime"
    assert 0.05 < rules["email"]["max_null_rate"] < 0.2  # the schema's 5% plus slack
    assert "nullable" not in rules["email"]
    # digit-only text and whole-number decimals profile as integers: no dtype is claimed for them
    assert "dtype" not in contract["tables"]["address"]["columns"]["zip_code"]
    assert "dtype" not in contract["tables"]["promotion"]["columns"]["discount_pct"]


def _violations(tables: dict[str, pa.Table], contract: dict) -> set[tuple[str, str]]:
    result = shape.check(shape.profile(tables, name=DOMAIN), contract)
    return {(v["column"] or "", v["rule"]) for v in result.violations}


def _tampered(retail, change) -> dict[str, pa.Table]:
    tables = dict(retail[0].tables)
    change(tables)
    return tables


def test_a_wrong_row_count_breaks_the_contract(retail):
    tables = _tampered(retail, lambda t: t.update(customer=t["customer"].slice(0, 900)))
    assert ("", "customer:row_count.min") in _violations(tables, retail[2])


def test_a_null_in_a_never_null_column_breaks_the_contract(retail):
    def nullify(tables):
        customer = tables["customer"]
        names = customer.column_names
        index = names.index("first_name")
        col = customer["first_name"].to_pylist()
        col[3] = None
        tables["customer"] = customer.set_column(
            index, "first_name", pa.array(col, customer.schema.field("first_name").type)
        )

    assert ("customer.first_name", "nullable") in _violations(_tampered(retail, nullify), retail[2])


def test_a_value_outside_an_enumerated_set_breaks_the_contract(retail):
    def intruder(tables):
        customer = tables["customer"]
        index = customer.column_names.index("loyalty_tier")
        col = customer["loyalty_tier"].to_pylist()
        col[0] = "Diamond"
        tables["customer"] = customer.set_column(index, "loyalty_tier", pa.array(col, pa.string()))

    got = _violations(_tampered(retail, intruder), retail[2])
    assert ("customer.loyalty_tier", "allowed_values") in got


def test_a_duplicate_primary_key_breaks_the_contract(retail):
    def duplicate(tables):
        customer = tables["customer"]
        index = customer.column_names.index("customer_id")
        col = customer["customer_id"].to_pylist()
        col[1] = col[0]
        tables["customer"] = customer.set_column(index, "customer_id", pa.array(col, pa.int64()))

    assert ("customer.customer_id", "unique") in _violations(
        _tampered(retail, duplicate), retail[2]
    )


def test_missing_extra_and_retyped_columns_and_missing_tables_break_the_contract(retail):
    def drop_column(tables):
        tables["customer"] = tables["customer"].drop_columns(["email"])

    assert ("customer.email", "required_column") in _violations(
        _tampered(retail, drop_column), retail[2]
    )

    def add_column(tables):
        tables["store"] = tables["store"].append_column(
            "surprise", pa.array([1] * tables["store"].num_rows)
        )

    assert ("store.surprise", "extra_column") in _violations(
        _tampered(retail, add_column), retail[2]
    )

    def retype(tables):
        customer = tables["customer"]
        index = customer.column_names.index("customer_id")
        ids = [f"id-{i}" for i in range(customer.num_rows)]
        tables["customer"] = customer.set_column(index, "customer_id", pa.array(ids, pa.string()))

    assert ("customer.customer_id", "dtype") in _violations(_tampered(retail, retype), retail[2])

    def drop_table(tables):
        del tables["return"]

    assert ("", "table_exists") in _violations(_tampered(retail, drop_table), retail[2])


def test_contract_rules_are_data_not_code(retail):
    # the contract is a plain dict that round-trips through JSON and is accepted by the checker
    contract = json.loads(json.dumps(copy.deepcopy(retail[2])))
    assert shape.check(shape.profile(dict(retail[0].tables), name=DOMAIN), contract).passed


# --------------------------------------------------------------- profile of a folder


def test_profile_tables_profiles_a_folder_as_a_dataset_the_contract_can_check(retail, tmp_path):
    import pyarrow.parquet as pq

    result, counts, contract = retail
    for name, table in result.tables.items():
        pq.write_table(table, tmp_path / f"{name}.parquet")
    profile = generation.profile_tables(tmp_path, DOMAIN)
    assert profile.is_dataset and set(profile.tables) == set(counts)
    assert shape.check(profile, contract).passed
    # a short table fails: the contract expects the planned rows
    pq.write_table(result.tables["customer"].slice(0, 900), tmp_path / "customer.parquet")
    failed = shape.check(generation.profile_tables(tmp_path, DOMAIN), contract)
    assert "customer:row_count.min" in {v["rule"] for v in failed.violations}


def test_profile_tables_refuses_a_folder_without_tables(tmp_path):
    with pytest.raises(generation.GenerationRequestError, match="no Parquet files"):
        generation.profile_tables(tmp_path)


def test_the_contract_expects_the_planned_rows_not_the_written_ones():
    planned = generation.plan_row_counts(DOMAIN, "small")
    schema = generation.generate_domain(DOMAIN, row_counts={"customer": 1}).schema
    contract = generation.domain_contract(schema, planned)
    assert contract["tables"]["customer"]["row_count"] == {"min": 1000, "max": 1000}
    short = generation.generate_domain(DOMAIN, scale="small", row_counts={"customer": 900})
    checked = shape.check(shape.profile(dict(short.tables), name=DOMAIN), contract)
    assert not checked.passed
    assert "customer:row_count.min" in {v["rule"] for v in checked.violations}


# ------------------------------------------------------------------ delta typing


def test_delta_ready_has_no_nanosecond_timestamps_or_large_strings():
    table = pa.table(
        {
            "t": pa.array([1, 2], pa.timestamp("ns")),
            "z": pa.array([1, 2], pa.timestamp("ns", tz="UTC")),
            "s": pa.array(["a", "b"], pa.large_string()),
            "n": pa.array([1, 2], pa.int64()),
        }
    )
    ready = generation.delta_ready(table)
    assert ready.schema.field("t").type == pa.timestamp("us")
    assert ready.schema.field("z").type == pa.timestamp("us", tz="UTC")
    assert ready.schema.field("s").type == pa.string()
    assert ready.schema.field("n").type == pa.int64()
    assert ready["n"].to_pylist() == [1, 2]


def test_write_delta_tables_validates_its_arguments(retail, tmp_path):
    with pytest.raises(generation.GenerationRequestError, match="tablePrefix"):
        generation.write_delta_tables(retail[0], tmp_path, prefix="../x")
    with pytest.raises(generation.GenerationRequestError, match="overwrite"):
        generation.write_delta_tables(retail[0], tmp_path, mode="merge")
    assert list(tmp_path.iterdir()) == []


# ----------------------------------------------------------------------- samples


def test_generate_sample_returns_the_asked_rows_of_one_table():
    table = generation.generate_sample(DOMAIN, "customer", rows=250, seed=3)
    assert table.num_rows == 250
    schema = generation.generate_domain(DOMAIN, row_counts={"customer": 1}).schema
    assert table.column_names == list(schema.tables["customer"].columns)
    again = generation.generate_sample(DOMAIN, "customer", rows=250, seed=3)
    assert table.equals(again)
    assert not table.equals(generation.generate_sample(DOMAIN, "customer", rows=250, seed=4))


def test_a_child_table_sample_keeps_its_foreign_keys_valid():
    sample = generation.generate_sample(DOMAIN, "order_line", rows=300, seed=1)
    assert sample.num_rows == 300
    parents = generation.sample_row_counts(
        generation.generate_domain(DOMAIN, row_counts={"customer": 1}).schema, "order_line", 300
    )
    assert parents["order_line"] == 300 and parents["order"] <= 300
    assert max(sample["order_id"].to_pylist()) <= parents["order"]
    assert min(sample["order_id"].to_pylist()) >= 1


def test_the_sample_of_a_table_equals_its_slice_of_the_same_run_for_a_root_table():
    sample = generation.generate_sample(DOMAIN, "customer", rows=40, seed=5)
    full = generation.generate_domain(DOMAIN, seed=5, row_counts={"customer": 40}).tables[
        "customer"
    ]
    assert sample.equals(full)


@pytest.mark.parametrize(
    "domain, table, rows, seed, message",
    [
        ("../retail", "customer", 10, 1, "domain"),
        ("retail", "customer; DROP TABLE x", 10, 1, "table"),
        ("retail", "../customer", 10, 1, "table"),
        ("nope", "customer", 10, 1, "no domain named"),
        ("retail", "nope", 10, 1, "no table 'nope'"),
        ("retail", "customer", 0, 1, "rows"),
        ("retail", "customer", -5, 1, "rows"),
        ("retail", "customer", True, 1, "rows"),
        ("retail", "customer", 10.5, 1, "rows"),
        ("retail", "customer", 10, "1", "seed"),
    ],
)
def test_the_sample_function_refuses_bad_arguments_with_a_user_visible_error(
    domain, table, rows, seed, message
):
    with pytest.raises(udf.UserThrownError) as caught:
        udf.generate_sample(domain, table, rows, seed)
    assert message in str(caught.value) or message in getattr(caught.value, "message", "")


def test_rows_are_capped_at_the_hard_ceiling(monkeypatch):
    monkeypatch.setattr(generation, "MAX_SAMPLE_ROWS", 120)
    assert generation.generate_sample(DOMAIN, "customer", rows=10_000, seed=1).num_rows == 120
    assert len(udf.generate_sample(DOMAIN, "customer", rows=10_000, seed=1)) == 120


def test_the_response_is_cut_to_fit_the_size_limit(monkeypatch):
    full = udf.generate_sample(DOMAIN, "customer", rows=2000, seed=1)
    assert len(full) == 2000
    limit = 60_000
    monkeypatch.setattr(generation, "MAX_RESPONSE_BYTES", limit)
    cut = generation.sample_to_pandas(DOMAIN, "customer", rows=2000, seed=1)
    assert 0 < len(cut) < 2000
    assert len(cut.to_json(orient="split", date_format="iso")) <= limit
    assert cut.equals(full.head(len(cut)))  # the leading rows, unchanged


def test_the_default_limit_is_under_the_30_mb_response_limit():
    assert generation.MAX_RESPONSE_BYTES < 30 * 1_000_000


def test_a_large_sample_stays_under_the_limit():
    frame = udf.generate_sample(DOMAIN, "order_line", rows=generation.MAX_SAMPLE_ROWS, seed=1)
    size = len(frame.to_json(orient="split", date_format="iso"))
    assert 0 < size <= generation.MAX_RESPONSE_BYTES < 30 * 1_000_000
