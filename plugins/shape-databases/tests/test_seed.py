"""`shape seed` (W5-05) through the real postgres and mysql sinks and the in-repo fake server."""

import json

import pytest
from shape_databases import MySqlSink, PostgresSink
from shape_databases.testing import FakeServer

from shape.testdata.seed import SeedRefused, seed_target

pytest.importorskip("shape_domains")

TARGETS = {
    "postgres": ("postgresql://shape@db.example:5432/shape", PostgresSink),
    "mysql": ("mysql://shape@db.example:3306/shape", MySqlSink),
}
ORDER = [
    "customer",
    "address",
    "product_category",
    "product",
    "promotion",
    "store",
    "order",
    "order_line",
    "return",
]


@pytest.fixture
def retail_schema(tmp_path):
    from shape.generation.domains import load_domain

    doc = load_domain("retail").schema.to_dict()
    for table in doc["tables"].values():
        for column in table["columns"].values():
            if column["type"] == "decimal":
                # Test author declaration, not a writer default or observed-value inference.
                column["precision"] = 18
                column["scale"] = 2
    path = tmp_path / "retail-explicit-decimals.json"
    path.write_text(json.dumps(doc))
    return str(path)


@pytest.fixture(params=["postgres", "mysql"])
def target(request):
    server = FakeServer(request.param)
    uri, sink = TARGETS[request.param]
    return request.param, uri, {request.param: sink(connect=server.connect)}, server


def tables_of(server):
    return {name: server.rows(name) for (_, name) in server.tables}


def test_seed_writes_the_tables_in_foreign_key_order_with_their_keys(target, retail_schema):
    name, uri, sinks, server = target
    result = seed_target(retail_schema, uri, scale="tiny", sinks=sinks)
    assert list(result.written) == ORDER and set(result.written.values()) == {100}
    assert [t for (_, t) in server.tables] == ORDER  # created in this order
    assert all(len(rows) == 100 for rows in tables_of(server).values())
    ddl = [e[1] for e in server.events if e[0] == "execute" and e[1].startswith("CREATE TABLE")]
    assert any("PRIMARY KEY" in d for d in ddl)


def test_create_checks_every_table_first_and_refuses_with_nothing_written(target, retail_schema):
    name, uri, sinks, server = target
    seed_target(retail_schema, uri, scale="tiny", sinks=sinks, mode="append")
    before = tables_of(server)
    with pytest.raises(SeedRefused, match="already exist"):
        seed_target(retail_schema, uri, scale="tiny", sinks=sinks)
    assert tables_of(server) == before


def test_create_refuses_one_late_table_without_creating_the_others(target, retail_schema):
    name, uri, sinks, server = target
    seed_target(retail_schema, uri, scale="tiny", sinks=sinks, mode="append")
    for key in [k for k in server.tables if k[1] != "return"]:
        del server.tables[key]  # only `return`, the last table, is left
    with pytest.raises(SeedRefused, match="table\\(s\\) return already exist"):
        seed_target(retail_schema, uri, scale="tiny", sinks=sinks)
    assert [t for (_, t) in server.tables] == ["return"]


def test_truncate_with_the_same_seed_leaves_identical_contents(target, retail_schema):
    name, uri, sinks, server = target
    seed_target(retail_schema, uri, scale="tiny", seed=9, sinks=sinks, mode="truncate")
    first = tables_of(server)
    seed_target(retail_schema, uri, scale="tiny", seed=9, sinks=sinks, mode="truncate")
    assert tables_of(server) == first and len(first) == 9
    seed_target(retail_schema, uri, scale="tiny", seed=10, sinks=sinks, mode="truncate")
    assert tables_of(server) != first


def test_append_adds_rows_and_refuses_keys_already_there(target, retail_schema):
    # Lead decision (2026-10-05): an append whose primary keys are already in the table is
    # refused before anything is written, naming the table and the key.
    name, uri, sinks, server = target
    seed_target(retail_schema, uri, scale="tiny", sinks=sinks, mode="append")
    assert all(len(rows) == 100 for rows in tables_of(server).values())
    before = tables_of(server)
    with pytest.raises(SeedRefused, match=r"customer \(customer_id = 1, and 99 more\)"):
        seed_target(retail_schema, uri, scale="tiny", sinks=sinks, mode="append")
    assert tables_of(server) == before
    for key in [k for k in server.tables if k[1] != "customer"]:
        del server.tables[key]
    customer = server.key(None, "customer")
    server.tables[customer] = [(r[0] + 10_000, *r[1:]) for r in server.tables[customer]]
    seed_target(retail_schema, uri, scale="tiny", sinks=sinks, mode="append")
    assert len(server.rows("customer")) == 200 and len(server.rows("order")) == 100
