import sqlite3

from shape.connectors import DBAPISink, DBAPISource


def test_dbapi():
    c = sqlite3.connect(":memory:")
    c.execute("create table x(id integer, name text)")
    n = DBAPISink(c, "insert into x values (?,?)", ("id", "name")).write(
        [[{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]]
    )
    assert n == 2
    batches = list(DBAPISource(c, "select * from x order by id", batch_size=1).rows())
    assert batches == [[{"id": 1, "name": "a"}], [{"id": 2, "name": "b"}]]
