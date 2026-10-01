"""Database profiling against the in-memory server (contract tests: no SQL Server needed)."""

import math
import statistics

import pytest
from shape_sqlserver import SqlServerError, profile_database
from shape_sqlserver.testing import FakeColumn, FakeConnection, FakeTable, scenario

pytestmark = pytest.mark.contract


@pytest.fixture(scope="module")
def retail():
    conn = scenario("retail")
    return conn, profile_database(connection=conn).to_dict()


def test_the_result_is_a_dataset_profile_with_every_table(retail):
    _, prof = retail
    assert sorted(prof["tables"]) == [
        "audit_log",
        "config",
        "customer",
        "order_item",
        "orders",
        "product",
    ]
    assert prof["tables"]["customer"]["row_count"] == 2500  # the catalog's count, not the sample's


def test_declared_keys_are_exact(retail):
    _, prof = retail
    assert prof["tables"]["order_item"]["primary_key"] == ["order_id", "line_no"]
    assert prof["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}
    cols = prof["tables"]["order_item"]["columns"]
    assert cols["order_id"]["is_primary_key"] and cols["order_id"]["is_foreign_key"]
    assert cols["order_id"]["fk_ref_table"] == "orders"
    assert cols["product_id"]["fk_ref_table"] == "product"
    assert not cols["quantity"]["is_foreign_key"]
    rels = {r["name"]: r for r in prof["relationships"]}
    assert rels["fk_orders_customer"] == {
        "name": "fk_orders_customer",
        "parent": "customer",
        "child": "orders",
        "parent_columns": ["customer_id"],
        "child_columns": ["customer_id"],
        "type": "one_to_many",
    }
    assert len(rels) == 3


def test_sql_types_map_to_shape_dtypes(retail):
    _, prof = retail
    cols = prof["tables"]["customer"]["columns"]
    assert {n: c["dtype"] for n, c in cols.items()} == {
        "customer_id": "integer",
        "name": "string",
        "email": "string",
        "segment": "string",
        "balance": "float",
        "credit_score": "integer",
        "created_at": "datetime",
        "birth_date": "date",
        "is_active": "boolean",
        "loyalty_points": "integer",
        "region_code": "string",
        "guid": "string",
    }


def test_statistics_describe_the_first_1000_rows(retail):
    conn, prof = retail
    rows = next(t for t in conn.tables if t.name == "customer").rows[:1000]
    col = prof["tables"]["customer"]["columns"]["credit_score"]
    scores = [r[5] for r in rows]
    assert col["cardinality"] == len(set(scores))
    assert col["min_value"] == ["float", float(min(scores))]
    assert col["max_value"] == ["float", float(max(scores))]
    assert col["mean"] == pytest.approx(statistics.fmean(scores), rel=1e-12)
    assert col["std"] == pytest.approx(statistics.stdev(scores), rel=1e-9)
    balance = [r[4] for r in rows]
    nulls = sum(v is None for v in balance)
    b = prof["tables"]["customer"]["columns"]["balance"]
    assert b["null_count"] == nulls > 0
    assert b["null_rate"] == nulls / 2500  # sample nulls over the table's row count


def test_ratios_use_the_table_row_count(retail):
    _, prof = retail
    cid = prof["tables"]["customer"]["columns"]["customer_id"]
    assert cid["cardinality"] == 1000
    assert cid["cardinality_ratio"] == 1000 / 2500
    assert cid["is_unique"] is False  # 0.4 <= 0.99: the sample is smaller than the table
    small = prof["tables"]["product"]["columns"]["product_id"]
    assert small["cardinality"] == 300 and small["is_unique"] is True


def test_enumerations_carry_observed_frequencies(retail):
    conn, prof = retail
    seg = prof["tables"]["customer"]["columns"]["segment"]
    assert seg["is_enum"] is True
    values = [r[3] for r in next(t for t in conn.tables if t.name == "customer").rows[:1000]]
    assert seg["enum_values"] == {v: pytest.approx(values.count(v) / 1000) for v in set(values)}
    counts = list(seg["enum_values"].values())
    assert counts == sorted(counts, reverse=True)  # most frequent first
    assert sum(seg["enum_values"].values()) == pytest.approx(1.0)
    flag = prof["tables"]["customer"]["columns"]["is_active"]
    assert set(flag["enum_values"]) <= {"true", "false"}  # booleans are lower-cased
    assert prof["tables"]["customer"]["columns"]["email"]["enum_values"] is None


def test_empty_and_single_row_tables(retail):
    _, prof = retail
    audit = prof["tables"]["audit_log"]
    assert audit["row_count"] == 0
    assert audit["primary_key"] == ["id"]
    assert all(c["cardinality"] == 0 and c["null_count"] == 0 for c in audit["columns"].values())
    assert not any(c["is_enum"] or c["is_unique"] for c in audit["columns"].values())
    cfg = prof["tables"]["config"]["columns"]["value"]
    assert cfg["std"] == "NaN" and cfg["mean"] == 3.0  # one value: no sample deviation


def test_profile_is_a_shape_profile_that_saves_and_loads(tmp_path, retail):
    import shape

    prof = profile_database(connection=scenario("retail"), tables=["orders"])
    path = tmp_path / "db.shape"
    shape.save(prof, path)
    assert shape.load(path) == prof
    assert list(prof.tables) == ["orders"]


def test_sample_rows_zero_profiles_the_catalog_only():
    conn = scenario("retail")
    prof = profile_database(connection=conn, sample_rows=0).to_dict()
    assert not any("SELECT TOP" in s for s in conn.statements)
    col = prof["tables"]["customer"]["columns"]["segment"]
    assert col["cardinality"] == 0 and col["enum_values"] is None
    assert prof["tables"]["customer"]["primary_key"] == ["customer_id"]
    assert len(prof["relationships"]) == 3


def test_the_sample_query_is_top_n_and_quoted():
    conn = scenario("retail")
    profile_database(connection=conn, sample_rows=25, tables=["config"])
    assert "SELECT TOP 25 * FROM [dbo].[config]" in conn.statements


def test_default_sample_is_1000_rows():
    conn = scenario("retail")
    profile_database(connection=conn, tables=["config"])
    assert "SELECT TOP 1000 * FROM [dbo].[config]" in conn.statements


def test_an_unreadable_table_still_gets_its_catalog_profile():
    conn = scenario("retail")
    conn.fail_reads = {"product"}
    prof = profile_database(connection=conn).to_dict()
    cols = prof["tables"]["product"]["columns"]
    assert prof["tables"]["product"]["row_count"] == 300
    assert cols["sku"]["cardinality"] == 0
    assert prof["tables"]["customer"]["columns"]["segment"]["cardinality"] == 4


def test_a_table_filter_keeps_only_those_tables():
    prof = profile_database(connection=scenario("retail"), tables=["orders", "customer"])
    assert sorted(prof.tables) == ["customer", "orders"]
    names = {r["name"] for r in prof.to_dict()["relationships"]}
    assert names == {"fk_orders_customer"}  # relationships of unprofiled children are dropped


def test_missing_primary_keys_are_guessed():
    conn = scenario("warehouse")
    prof = profile_database(connection=conn).to_dict()
    assert prof["tables"]["dimcustomer"]["primary_key"] == ["customer_key"]
    assert prof["tables"]["dimproduct"]["primary_key"] == ["product_key"]
    assert prof["tables"]["factsales"]["primary_key"] == ["sale_id"]


def test_identity_column_wins_the_key_guess():
    t = FakeTable(
        "things",
        [
            FakeColumn("name", "varchar"),
            FakeColumn("seq", "int", identity=True),
            FakeColumn("id", "int"),
        ],
        [("a", 1, 1)],
    )
    prof = profile_database(connection=FakeConnection([t])).to_dict()
    assert prof["tables"]["things"]["primary_key"] == ["seq"]


def test_without_declared_foreign_keys_names_link_tables_that_the_data_does_not():
    prof = profile_database(connection=scenario("name_only")).to_dict()
    assert prof["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}
    assert [r["name"] for r in prof["relationships"]] == ["fk_orders_customer_id"]
    assert prof["relationships"][0]["parent_columns"] == ["id"]  # guessed key of customer


def test_name_inference_prefixes_dimension_tables():
    prof = profile_database(connection=scenario("warehouse_empty")).to_dict()
    assert prof["tables"]["factsales"]["detected_fks"] == {
        "customer_key": "dimcustomer",
        "product_key": "dimproduct",
    }


def test_when_the_data_already_shows_a_relationship_names_are_not_used():
    # the sampled rows hold the keys of the parent table, so the data decides, and this
    # profiler reports no inferred relationships for it
    prof = profile_database(connection=scenario("id_named")).to_dict()
    assert prof["relationships"] == []
    assert prof["tables"]["orders"]["detected_fks"] == {}


def test_a_passed_connection_is_reused_and_left_open():
    conn = scenario("retail")
    profile_database(connection=conn, tables=["config"])
    assert conn.closed is False


def test_an_owned_connection_is_closed(monkeypatch):
    conn = scenario("retail")
    monkeypatch.setattr("shape_sqlserver.profiler.connect", lambda text, creds: conn)
    profile_database("Server=x", tables=["config"])
    assert conn.closed is True


@pytest.mark.parametrize("bad", [-1, True, 1.5, "10"])
def test_sample_rows_must_be_a_non_negative_int(bad):
    with pytest.raises(SqlServerError, match="sample_rows"):
        profile_database(connection=scenario("retail"), sample_rows=bad)


def test_a_connection_or_a_string_is_required():
    with pytest.raises(SqlServerError, match="connection"):
        profile_database()


def test_hostile_table_names_are_quoted():
    t = FakeTable("a]; DROP TABLE x;--", [FakeColumn("v", "int")], [(1,)])
    conn = FakeConnection([t])
    prof = profile_database(connection=conn).to_dict()
    assert prof["tables"][t.name]["columns"]["v"]["cardinality"] == 1
    assert "SELECT TOP 1000 * FROM [dbo].[a]]; DROP TABLE x;--]" in conn.statements


def test_nan_free_numbers_for_every_numeric_column(retail):
    _, prof = retail
    for table in prof["tables"].values():
        for col in table["columns"].values():
            for field in ("mean", "std", "null_rate", "cardinality_ratio"):
                v = col[field]
                assert v is None or v == "NaN" or math.isfinite(v)


# ---- P1-18: an enumeration has repeating values (distinct <= 0.5 x sampled non-null values) ----
def _enum_profile(values, type_name="varchar", sample_rows=None, **kw):
    t = FakeTable("t", [FakeColumn("c", type_name)], [(v,) for v in values])
    if sample_rows is not None:
        kw["sample_rows"] = sample_rows
    return profile_database(connection=FakeConnection([t]), **kw).to_dict()["tables"]["t"][
        "columns"
    ]["c"]


def test_a_unique_column_is_not_an_enumeration():
    col = _enum_profile([f"user{i}" for i in range(40)])  # 40 <= 50 distinct, but all different
    assert col["cardinality"] == 40
    assert col["is_enum"] is False and col["enum_values"] is None


def test_a_tiny_table_of_distinct_text_is_not_an_enumeration():
    col = _enum_profile(list("abcdefghij"))
    assert col["is_enum"] is False and col["enum_values"] is None


def test_free_text_is_not_an_enumeration():
    col = _enum_profile([f"note about order {i % 90}" for i in range(120)])  # 90 > 0.5 x 120
    assert col["is_enum"] is False and col["enum_values"] is None


def test_a_low_cardinality_category_stays_an_enumeration():
    col = _enum_profile(["red", "green", "blue"] * 20)
    assert col["is_enum"] is True
    assert col["enum_values"] == {"red": 1 / 3, "green": 1 / 3, "blue": 1 / 3}


@pytest.mark.parametrize("distinct,expected", [(4, True), (5, True), (6, False)])
def test_the_half_boundary(distinct, expected):
    values = [f"v{i % distinct}" for i in range(distinct)] + ["v0"] * (10 - distinct)
    col = _enum_profile(values)
    assert col["cardinality"] == distinct
    assert col["is_enum"] is expected
    assert (col["enum_values"] is not None) is expected


def test_the_boundary_counts_sampled_non_null_values():
    # 5 distinct among 8 non-null values (two nulls): more than half
    assert _enum_profile(list("abcde") + list("abc") + [None, None])["is_enum"] is False
    assert _enum_profile(list("abcd") * 2 + [None, None])["is_enum"] is True


def test_the_boundary_uses_the_sample_not_the_table():
    # 30 rows, a sample of 12 holding 7 distinct values: 7 > 6, not an enumeration
    values = [f"v{i}" for i in range(7)] + ["v0"] * 5 + ["v0"] * 18
    assert _enum_profile(values, sample_rows=12)["is_enum"] is False
    assert _enum_profile(values, sample_rows=30)["is_enum"] is True  # 7 of 30


def test_all_null_and_one_value_columns():
    nulls = _enum_profile([None] * 10)
    assert nulls["is_enum"] is False and nulls["enum_values"] is None
    same = _enum_profile(["x"] * 10)
    assert same["is_enum"] is True and same["enum_values"] == {"x": 1.0}
    assert _enum_profile(["x"])["is_enum"] is False  # one row: unique
