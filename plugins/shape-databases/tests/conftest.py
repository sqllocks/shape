import pytest
from shape_databases import MySqlSink, PostgresSink
from shape_databases.testing import FakeServer

PG_URI = "postgresql://shape@db.example:5432/shape?sslmode=require"
MY_URI = "mysql://shape@db.example:3306/shape"


@pytest.fixture(params=["postgres", "mysql"])
def flavour(request):
    """(sink, uri, server): the same behaviour must hold for both sinks."""
    server = FakeServer(request.param)
    if request.param == "postgres":
        return PostgresSink(connect=server.connect), PG_URI, server
    return MySqlSink(connect=server.connect), MY_URI, server
