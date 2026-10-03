"""`shape seed` (W5-05) through the real postgres and mysql sinks and the in-repo fake server."""

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


@pytest.fixture(params=["postgres", "mysql"])
def target(request):
    server = FakeServer(request.param)
    uri, sink = TARGETS[request.param]
    return request.param, uri, {request.param: sink(connect=server.connect)}, server


def tables_of(server):
    return {name: server.rows(name) for (_, name) in server.tables}


def test_seed_writes_the_tables_in_foreign_key_order_with_their_keys(target):
    name, uri, sinks, server = target
    result = seed_target("retail", uri, scale="tiny", sinks=sinks)
    assert list(result.written) == ORDER and set(result.written.values()) == {100}
    assert [t for (_, t) in server.tables] == ORDER  # created in this order
    assert all(len(rows) == 100 for rows in tables_of(server).values())
    ddl = [e[1] for e in server.events if e[0] == "execute" and e[1].startswith("CREATE TABLE")]
    assert any("PRIMARY KEY" in d for d in ddl)


def test_create_checks_every_table_first_and_refuses_with_nothing_written(target):
    name, uri, sinks, server = target
    seed_target("retail", uri, scale="tiny", sinks=sinks, mode="append")
    before = tables_of(server)
    with pytest.raises(SeedRefused, match="already exist"):
        seed_target("retail", uri, scale="tiny", sinks=sinks)
    assert tables_of(server) == before


def test_create_refuses_one_late_table_without_creating_the_others(target):
    name, uri, sinks, server = target
    seed_target("retail", uri, scale="tiny", sinks=sinks, mode="append")
    for key in [k for k in server.tables if k[1] != "return"]:
        del server.tables[key]  # only `return`, the last table, is left
    with pytest.raises(SeedRefused, match="table\\(s\\) return already exist"):
        seed_target("retail", uri, scale="tiny", sinks=sinks)
    assert [t for (_, t) in server.tables] == ["return"]


def test_truncate_with_the_same_seed_leaves_identical_contents(target):
    name, uri, sinks, server = target
    seed_target("retail", uri, scale="tiny", seed=9, sinks=sinks, mode="truncate")
    first = tables_of(server)
    seed_target("retail", uri, scale="tiny", seed=9, sinks=sinks, mode="truncate")
    assert tables_of(server) == first and len(first) == 9
    seed_target("retail", uri, scale="tiny", seed=10, sinks=sinks, mode="truncate")
    assert tables_of(server) != first


def test_append_adds_rows(target):
    name, uri, sinks, server = target
    for _ in range(2):
        seed_target("retail", uri, scale="tiny", sinks=sinks, mode="append")
    assert all(len(rows) == 200 for rows in tables_of(server).values())
