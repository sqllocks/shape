"""Database profiling against the in-memory server (contract tests: no SQL Server needed)."""

import math
import statistics

import pytest
from shape_sqlserver import SqlServerError, profile_database
from shape_sqlserver.testing import FakeColumn, FakeConnection, FakeTable, scenario, spread_hash

pytestmark = pytest.mark.contract


def spread(table, n, key=None):
    """The rows the documented method picks: the ``n`` smallest scrambled ``CHECKSUM(key)`` (all
    columns for a table with no key), in that order."""
    names = [c.name for c in table.columns]
    cols = [names.index(c) for c in (key or names)]
    return sorted(table.rows, key=lambda r: spread_hash(tuple(r[i] for i in cols)))[:n]


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
        "evidence": "declared",
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


def test_statistics_describe_the_sampled_rows(retail):
    conn, prof = retail
    customer = next(t for t in conn.tables if t.name == "customer")
    rows = spread(customer, 1000, ["customer_id"])
    assert rows != customer.rows[:1000]
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
    assert b["null_rate"] == nulls / 1000  # sample nulls over the rows sampled


def test_ratios_divide_by_the_rows_sampled(retail):
    _, prof = retail
    cid = prof["tables"]["customer"]["columns"]["customer_id"]
    assert prof["tables"]["customer"]["row_count"] == 2500
    assert cid["cardinality"] == 1000
    assert cid["cardinality_ratio"] == 1.0  # 1000 distinct values in 1000 sampled rows
    assert cid["is_unique"] is True  # sample-based: true although the table is larger
    small = prof["tables"]["product"]["columns"]["product_id"]
    assert small["cardinality"] == 300 and small["is_unique"] is True
    assert small["cardinality_ratio"] == 1.0


def test_sample_based_statistics_are_labelled(retail):
    _, prof = retail
    assert prof["tables"]["customer"]["sampled_rows"] == 1000
    assert prof["tables"]["product"]["sampled_rows"] == 300  # a table smaller than the sample
    assert prof["tables"]["audit_log"]["sampled_rows"] == 0
    sampling = prof["sampling"]
    assert sampling["requested_rows"] == 1000 and "CHECKSUM" in sampling["method"]
    assert "sampled rows" in sampling["note"]


def test_a_duplicated_column_is_not_unique_and_ratios_follow_the_sample():
    rows = [(i, i % 4, None if i % 5 == 0 else 1) for i in range(400)]
    t = FakeTable(
        "t",
        [FakeColumn("a", "int"), FakeColumn("b", "int"), FakeColumn("c", "int")],
        rows,
    )
    prof = profile_database(connection=FakeConnection([t]), sample_rows=100).to_dict()
    cols = prof["tables"]["t"]["columns"]
    assert prof["tables"]["t"]["sampled_rows"] == 100
    assert cols["a"]["cardinality_ratio"] == 1.0 and cols["a"]["is_unique"] is True
    assert cols["b"]["cardinality_ratio"] == 0.04 and cols["b"]["is_unique"] is False
    nulls = sum(r[2] is None for r in spread(t, 100))
    assert cols["c"]["null_count"] == nulls > 0 and cols["c"]["null_rate"] == nulls / 100


def test_sampled_rows_is_zero_without_a_sample():
    prof = profile_database(connection=scenario("retail"), sample_rows=0).to_dict()
    assert {t["sampled_rows"] for t in prof["tables"].values()} == {0}
    assert prof["sampling"]["requested_rows"] == 0
    conn = scenario("retail")
    conn.fail_reads = {"product"}
    prof = profile_database(connection=conn).to_dict()
    assert prof["tables"]["product"]["sampled_rows"] == 0


def test_the_labelled_profile_saves_loads_and_renders(tmp_path):
    import shape

    prof = profile_database(connection=scenario("retail"), tables=["orders", "customer"])
    path = tmp_path / "db.shape"
    shape.save(prof, path)
    again = shape.load(path)
    assert again == prof and again.to_dict()["sampling"]["requested_rows"] == 1000
    assert "customer" in again.to_html() and again.summary()["tables"]["orders"]


def test_enumerations_carry_observed_frequencies(retail):
    conn, prof = retail
    seg = prof["tables"]["customer"]["columns"]["segment"]
    assert seg["is_enum"] is True
    customer = next(t for t in conn.tables if t.name == "customer")
    values = [r[3] for r in spread(customer, 1000, ["customer_id"])]
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
    assert all(c["is_unique"] is None for c in audit["columns"].values())  # nothing to judge by
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


def test_name_inferred_keys_mark_the_column_as_a_foreign_key():
    prof = profile_database(connection=scenario("name_only")).to_dict()
    col = prof["tables"]["orders"]["columns"]["customer_id"]
    assert col["is_foreign_key"] is True and col["fk_ref_table"] == "customer"
    assert prof["tables"]["orders"]["columns"]["amount"]["is_foreign_key"] is False
    assert prof["tables"]["customer"]["columns"]["id"]["is_foreign_key"] is False
    wh = profile_database(connection=scenario("warehouse_empty")).to_dict()
    fact = wh["tables"]["factsales"]
    assert (
        {c for c, v in fact["columns"].items() if v["is_foreign_key"]}
        == set(fact["detected_fks"])
        == {"customer_key", "product_key"}
    )
    assert fact["columns"]["product_key"]["fk_ref_table"] == "dimproduct"


def test_name_inference_prefixes_dimension_tables():
    prof = profile_database(connection=scenario("warehouse_empty")).to_dict()
    assert prof["tables"]["factsales"]["detected_fks"] == {
        "customer_key": "dimcustomer",
        "product_key": "dimproduct",
    }


def test_a_relationship_the_data_shows_is_reported_without_declared_keys():
    # no declared keys: the sampled rows of orders.customer_id all exist in customer
    prof = profile_database(connection=scenario("id_named")).to_dict()
    assert prof["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}
    assert [r["name"] for r in prof["relationships"]] == ["fk_orders_customer_id"]
    rel = prof["relationships"][0]
    assert (rel["parent"], rel["child"]) == ("customer", "orders")
    assert (rel["parent_columns"], rel["child_columns"]) == (["customer_id"], ["customer_id"])
    col = prof["tables"]["orders"]["columns"]["customer_id"]
    assert col["is_foreign_key"] is True and col["fk_ref_table"] == "customer"
    assert prof["tables"]["customer"]["columns"]["customer_id"]["is_foreign_key"] is False


def test_data_evidence_wins_and_names_fill_the_remaining_columns():
    # orders.customer_id holds customer keys (the data confirms the name); orders.product_key
    # holds numbers no product has and has no `_id` form (only its name suggests the link);
    # orders.region_id has no matching table
    customer = FakeTable(
        "customer",
        [FakeColumn("id", "int", nullable=False), FakeColumn("label", "varchar")],
        [(i, f"c{i}") for i in range(1, 51)],
    )
    product = FakeTable(
        "product",
        [FakeColumn("product_key", "int", nullable=False), FakeColumn("label", "varchar")],
        [(i, f"p{i}") for i in range(1, 11)],
    )
    orders = FakeTable(
        "orders",
        [
            FakeColumn("order_id", "int", nullable=False),
            FakeColumn("customer_id", "int"),
            FakeColumn("product_key", "int"),
            FakeColumn("region_id", "int"),
        ],
        [(i, 1 + i % 50, 900 + i % 7, 5000 + i % 3) for i in range(1, 201)],
    )
    prof = profile_database(connection=FakeConnection([customer, product, orders])).to_dict()
    assert prof["tables"]["orders"]["detected_fks"] == {
        "customer_id": "customer",
        "product_key": "product",
    }
    rels = {r["name"]: r for r in prof["relationships"]}
    assert list(rels) == ["fk_orders_customer_id", "fk_orders_product_key"]  # data first
    assert rels["fk_orders_customer_id"]["parent_columns"] == ["id"]
    assert rels["fk_orders_product_key"]["parent_columns"] == ["product_key"]
    cols = prof["tables"]["orders"]["columns"]
    assert [c for c, v in cols.items() if v["is_foreign_key"]] == ["customer_id", "product_key"]
    assert cols["product_key"]["fk_ref_table"] == "product"
    assert not cols["region_id"]["is_foreign_key"]


def test_a_column_the_data_links_is_not_linked_a_second_time_by_name():
    prof = profile_database(connection=scenario("id_named")).to_dict()
    assert len(prof["relationships"]) == 1


def test_declared_keys_are_authoritative_and_nothing_is_inferred_beside_them():
    prof = profile_database(connection=scenario("retail")).to_dict()
    assert len(prof["relationships"]) == 3
    assert prof["tables"]["orders"]["detected_fks"] == {"customer_id": "customer"}


def test_dim_and_fact_inside_a_table_name_are_not_stripped():
    from shape_sqlserver.catalog import table_stem

    assert table_stem("dimcustomer") == "customer" and table_stem("Fact_Sales") == "sales"
    assert table_stem("sandimas") == "sandimas" and table_stem("manufacturer") == "manufacturer"
    assert table_stem("dim") == "dim"
    t = FakeTable("sandimas", [FakeColumn("v", "int"), FakeColumn("sandimas_id", "int")], [(1, 1)])
    prof = profile_database(connection=FakeConnection([t])).to_dict()
    assert prof["tables"]["sandimas"]["primary_key"] == ["sandimas_id"]


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


def test_all_null_and_one_value_columns():
    nulls = _enum_profile([None] * 10)
    assert nulls["is_enum"] is False and nulls["enum_values"] is None
    same = _enum_profile(["x"] * 10)
    assert same["is_enum"] is True and same["enum_values"] == {"x": 1.0}
    assert _enum_profile(["x"])["is_enum"] is False  # one row: unique


# --- FIX-4: a deterministic sample spread over the whole table ------------------------------


def _ordered(n=5000):
    return FakeTable(
        "events",
        [FakeColumn("id", "int", nullable=False), FakeColumn("day", "int")],
        [(i, i // 50) for i in range(n)],  # stored in key (and date) order
        primary_key=("id",),
    )


def test_the_sample_is_spread_over_the_whole_table_not_its_first_rows():
    prof = profile_database(connection=FakeConnection([_ordered()]), sample_rows=200).to_dict()
    ids = prof["tables"]["events"]["columns"]["id"]
    assert prof["tables"]["events"]["sampled_rows"] == 200
    assert ids["max_value"][1] > 4000 and ids["min_value"][1] < 1000  # first 200 rows: 0..199
    assert 2000 < ids["mean"] < 3000  # the table's own mean is 2499.5
    days = prof["tables"]["events"]["columns"]["day"]
    assert days["max_value"][1] > 80  # a table stored by date is not sampled from its start


def test_the_same_data_gives_the_same_sample_on_every_run():
    first = profile_database(connection=FakeConnection([_ordered()]), sample_rows=200).to_dict()
    again = profile_database(connection=FakeConnection([_ordered()]), sample_rows=200).to_dict()
    assert first == again
    table = _ordered()
    ids = first["tables"]["events"]["columns"]["id"]
    assert ids["mean"] == pytest.approx(statistics.fmean(r[0] for r in spread(table, 200, ["id"])))


def test_the_spread_sample_orders_by_a_checksum_of_the_key():
    conn = FakeConnection([_ordered()])
    profile_database(connection=conn, sample_rows=200)
    assert (
        "SELECT TOP 200 * FROM [dbo].[events] "
        "ORDER BY (CAST(CHECKSUM([id]) AS bigint) * 1327217885) % 2147483647, [id]"
    ) in conn.statements


def test_a_heap_without_a_key_is_sampled_on_every_hashable_column():
    t = FakeTable(
        "heap",
        [
            FakeColumn("a", "int"),
            FakeColumn("body", "xml"),  # CHECKSUM cannot hash it
            FakeColumn("b", "varchar"),
        ],
        [(i, "<x/>", f"v{i % 9}") for i in range(600)],
    )
    conn = FakeConnection([t])
    prof = profile_database(connection=conn, sample_rows=100).to_dict()
    assert any("CHECKSUM([a], [b]) AS bigint" in stmt for stmt in conn.statements)
    assert prof["tables"]["heap"]["sampled_rows"] == 100
    assert prof["tables"]["heap"]["columns"]["a"]["max_value"][1] > 300  # not the first 100 rows
    assert prof["tables"]["heap"]["sample_method"] == "checksum spread"


def test_a_table_no_larger_than_the_sample_is_read_whole():
    conn = FakeConnection([_ordered(50)])
    prof = profile_database(connection=conn, sample_rows=200).to_dict()
    assert "SELECT TOP 200 * FROM [dbo].[events]" in conn.statements
    assert not any("CHECKSUM" in s for s in conn.statements)
    assert prof["tables"]["events"]["sample_method"] == "all rows"
    assert prof["tables"]["events"]["sampled_rows"] == 50


def test_the_profile_records_how_each_table_was_sampled():
    prof = profile_database(connection=scenario("retail"), sample_rows=200).to_dict()
    methods = {n: t["sample_method"] for n, t in prof["tables"].items()}
    assert methods["customer"] == "checksum spread"
    assert methods["config"] == "all rows"
    assert methods["audit_log"] == "all rows"  # an empty table is read whole
    assert "CHECKSUM" in prof["sampling"]["method"]
    none = profile_database(connection=scenario("retail"), sample_rows=0).to_dict()
    assert {t["sample_method"] for t in none["tables"].values()} == {"none"}


def test_a_server_that_cannot_hash_falls_back_to_the_first_rows():
    class NoChecksum(FakeConnection):
        def dispatch(self, cur, sql, params):
            if "CHECKSUM" in sql:
                raise RuntimeError("CHECKSUM is not supported")
            super().dispatch(cur, sql, params)

    prof = profile_database(connection=NoChecksum([_ordered()]), sample_rows=200).to_dict()
    table = prof["tables"]["events"]
    assert table["sample_method"] == "first rows (fallback)" and table["sampled_rows"] == 200
    assert table["columns"]["id"]["max_value"][1] == 199  # said so, not hidden


# --- FIX-5: no sampled rows means unknown, not zero ----------------------------------------


def test_a_column_with_no_sampled_rows_reports_null_not_zero():
    prof = profile_database(connection=scenario("retail"), sample_rows=0).to_dict()
    for table in prof["tables"].values():
        for col in table["columns"].values():
            assert col["null_rate"] is None, col["name"]
            assert col["cardinality_ratio"] is None and col["is_unique"] is None
    conn = scenario("retail")
    conn.fail_reads = {"product"}
    prof = profile_database(connection=conn).to_dict()
    sku = prof["tables"]["product"]["columns"]["sku"]  # unreadable
    assert (sku["null_rate"], sku["cardinality_ratio"], sku["is_unique"]) == (None, None, None)
    audit = prof["tables"]["audit_log"]["columns"]["id"]  # empty table: nothing sampled
    assert (audit["null_rate"], audit["cardinality_ratio"], audit["is_unique"]) == (
        None,
        None,
        None,
    )
    known = prof["tables"]["customer"]["columns"]["balance"]
    assert known["null_rate"] is not None and known["cardinality_ratio"] is not None


def test_the_unknown_values_save_load_render_and_check(tmp_path):
    import shape

    prof = profile_database(connection=scenario("retail"), sample_rows=0)
    path = tmp_path / "db.shape"
    shape.save(prof, path)
    again = shape.load(path)
    assert again == prof
    assert "n/a" in again.to_html() and again.summary()["tables"]["customer"]
    contract = {"tables": {"customer": {"columns": {"customer_id": {"unique": True}}}}}
    assert shape.check(again, contract).passed
    assert not shape.diff(again, again).drifted


# --- FIX-6: is_unique is null-aware and needs two values ------------------------------------


def _one_column(values, **kw):
    t = FakeTable("t", [FakeColumn("v", "int")], [(v,) for v in values])
    prof = profile_database(connection=FakeConnection([t]), **kw).to_dict()
    return prof["tables"]["t"]["columns"]["v"]


def test_unique_among_the_non_null_values_even_with_nulls():
    col = _one_column([None if i % 5 == 0 else i for i in range(100)])
    assert col["null_rate"] == 0.2
    assert col["is_unique"] is True  # 80 distinct values among 80 non-null ones


def test_a_repeated_value_is_not_unique():
    assert _one_column([1, 2, 2, 3, 4, 5, 6, 7, 8, 9])["is_unique"] is False


def test_one_row_does_not_make_a_column_unique():
    assert _one_column([7])["is_unique"] is None
    assert _one_column([7, None, None])["is_unique"] is None  # one non-null value
    assert _one_column([None, None])["is_unique"] is None  # no non-null value
    assert _one_column([7, 8])["is_unique"] is True
    assert _one_column([7, 8, 9], sample_rows=1)["is_unique"] is None  # a 1-row sample


def test_every_column_of_a_one_row_sample_is_unknown_not_unique():
    prof = profile_database(connection=scenario("retail"), sample_rows=1).to_dict()
    uniques = {c["is_unique"] for c in prof["tables"]["customer"]["columns"].values()}
    assert uniques == {None}


# --- FIX-7: declared keys stay authoritative, undeclared ones are still inferred ------------


def test_undeclared_keys_are_inferred_beside_declared_ones_and_say_where_from():
    prof = profile_database(connection=scenario("mixed_keys")).to_dict()
    rels = {r["name"]: r for r in prof["relationships"]}
    assert list(rels) == ["fk_orders_customer", "fk_orders_product_id", "fk_orders_region_key"]
    assert [r["evidence"] for r in rels.values()] == ["declared", "data", "name"]
    assert rels["fk_orders_customer"]["parent_columns"] == ["customer_id"]  # as declared
    assert rels["fk_orders_product_id"]["parent_columns"] == ["product_id"]
    assert rels["fk_orders_region_key"]["parent_columns"] == ["region_id"]
    orders = prof["tables"]["orders"]
    assert orders["detected_fks"] == {
        "customer_id": "customer",
        "product_id": "product",
        "region_key": "region",
    }
    cols = orders["columns"]
    assert {c: v["fk_evidence"] for c, v in cols.items()} == {
        "order_id": None,
        "customer_id": "declared",
        "product_id": "data",
        "region_key": "name",
    }
    assert all(v["is_foreign_key"] for c, v in cols.items() if v["fk_evidence"])
    assert cols["region_key"]["fk_ref_table"] == "region"


def test_a_declared_key_is_never_overridden_by_inference():
    # orders.customer_id is declared to customer; no other evidence replaces it
    prof = profile_database(connection=scenario("mixed_keys")).to_dict()
    assert prof["tables"]["orders"]["columns"]["customer_id"]["fk_ref_table"] == "customer"
    assert sum(r["child_columns"] == ["customer_id"] for r in prof["relationships"]) == 1


def test_a_fully_declared_schema_infers_nothing_more():
    prof = profile_database(connection=scenario("retail")).to_dict()
    assert {r["evidence"] for r in prof["relationships"]} == {"declared"}
    fk = {c: v["fk_evidence"] for c, v in prof["tables"]["orders"]["columns"].items()}
    assert fk["customer_id"] == "declared"
    assert [r["name"] for r in prof["relationships"] if r["child"] == "orders"] == [
        "fk_orders_customer"
    ]


def test_each_inferred_link_carries_its_evidence_without_declared_keys():
    nom = profile_database(connection=scenario("name_only")).to_dict()
    assert [r["evidence"] for r in nom["relationships"]] == ["name"]
    idn = profile_database(connection=scenario("id_named")).to_dict()
    assert [r["evidence"] for r in idn["relationships"]] == ["data"]
    assert idn["tables"]["orders"]["columns"]["customer_id"]["fk_evidence"] == "data"
    assert idn["tables"]["orders"]["columns"]["amount"]["fk_evidence"] is None


# --- FIX-8: id and key only as whole words --------------------------------------------------


@pytest.mark.parametrize(
    ("column", "stem"),
    [
        ("customer_id", "customer"),
        ("CustomerId", "customer"),
        ("customerID", "customer"),
        ("CUSTOMER_ID", "customer"),
        ("customer_key", "customer"),
        ("CustomerKey", "customer"),
        ("sales_region_id", "sales_region"),
        ("SalesRegionID", "sales_region"),
        ("paid", None),
        ("valid", None),
        ("monkey", None),
        ("hid", None),
        ("customerid", None),  # one word: no boundary to split at
        ("id", None),
        ("key", None),
        ("_id", None),
        ("identity", None),
        ("pa_id", "pa"),
    ],
)
def test_key_stem_matches_id_and_key_only_as_whole_words(column, stem):
    from shape_sqlserver.catalog import key_stem

    assert key_stem(column) == stem


def test_paid_and_valid_are_not_linked_to_tables_pa_and_val():
    prof = profile_database(connection=scenario("key_names")).to_dict()
    invoices = prof["tables"]["invoices"]
    assert "paid" not in invoices["detected_fks"] and "valid" not in invoices["detected_fks"]
    assert not invoices["columns"]["paid"]["is_foreign_key"]
    assert not invoices["columns"]["valid"]["is_foreign_key"]
    assert {r["child"] for r in prof["relationships"]} == {"invoices", "notes"}
    assert all(r["parent"] == "customer" for r in prof["relationships"])


def test_camel_case_columns_link_to_their_table():
    parent = FakeTable("Customer", [FakeColumn("Id", "int", nullable=False)], [(1,), (2,)])
    child = FakeTable(
        "Orders",
        [FakeColumn("OrderId", "int", nullable=False), FakeColumn("CustomerId", "int")],
        [(1, 1), (2, 2), (3, 9)],
    )
    prof = profile_database(connection=FakeConnection([parent, child])).to_dict()
    assert prof["tables"]["Orders"]["detected_fks"] == {"CustomerId": "Customer"}
    assert prof["tables"]["Orders"]["primary_key"] == ["OrderId"]


# --- FIX-9: a guessed key is never a foreign key --------------------------------------------


def test_a_guessed_key_is_not_a_foreign_key_and_prefers_the_table_s_own_id():
    prof = profile_database(connection=scenario("key_names")).to_dict()
    # customer_id comes first in invoices but links to customer: the key is invoice_id
    assert prof["tables"]["invoices"]["primary_key"] == ["invoice_id"]
    assert prof["tables"]["invoices"]["columns"]["customer_id"]["is_primary_key"] is False
    assert prof["tables"]["invoices"]["columns"]["invoice_id"]["is_primary_key"] is True
    assert prof["tables"]["customer"]["primary_key"] == ["customer_id"]
    assert prof["tables"]["pa"]["primary_key"] == ["id"]


def test_a_table_with_no_plausible_key_reports_none():
    prof = profile_database(connection=scenario("key_names")).to_dict()
    notes = prof["tables"]["notes"]
    assert notes["primary_key"] == []  # note (text) and customer_id (a foreign key)
    assert not any(c["is_primary_key"] for c in notes["columns"].values())
    t = FakeTable("things", [FakeColumn("name", "varchar"), FakeColumn("n", "int")], [("a", 1)])
    assert (
        profile_database(connection=FakeConnection([t])).to_dict()["tables"]["things"][
            "primary_key"
        ]
        == []
    )


def test_a_candidate_key_must_be_unique_and_not_null_in_the_sample():
    rows = [(i % 10, i if i != 5 else None) for i in range(50)]
    t = FakeTable("things", [FakeColumn("id", "int"), FakeColumn("things_id", "int")], rows)
    prof = profile_database(connection=FakeConnection([t])).to_dict()
    assert prof["tables"]["things"]["primary_key"] == []  # id repeats, things_id has a null
    ok = FakeTable("things", [FakeColumn("id", "int"), FakeColumn("things_id", "int")], rows[:0])
    # with no sampled rows nothing rules the named column out
    assert profile_database(connection=FakeConnection([ok])).to_dict()["tables"]["things"][
        "primary_key"
    ] == ["id"]


def test_a_name_inferred_foreign_key_is_not_guessed_as_the_key():
    # product_id is only name-linked (its values are not product ids), first in its table
    product = FakeTable("product", [FakeColumn("product_id", "int", nullable=False)], [(1,), (2,)])
    line = FakeTable(
        "lines",
        [FakeColumn("product_id", "int"), FakeColumn("line_id", "int")],
        [(800 + i, i) for i in range(20)],
    )
    prof = profile_database(connection=FakeConnection([product, line])).to_dict()
    assert prof["tables"]["lines"]["detected_fks"] == {"product_id": "product"}
    assert prof["tables"]["lines"]["primary_key"] == ["line_id"]


def test_declared_keys_are_never_replaced_by_a_guess():
    prof = profile_database(connection=scenario("mixed_keys")).to_dict()
    assert prof["tables"]["orders"]["primary_key"] == ["order_id"]
    assert prof["tables"]["region"]["primary_key"] == ["region_id"]
