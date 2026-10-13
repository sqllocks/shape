"""A concurrent table creation cannot invalidate the fake's snapshot iterator."""

from shape_fabric.testing import FakeSqlServer


def test_snapshot_captures_the_table_mapping_before_copying_table_contents():
    server = FakeSqlServer()

    class CreatingTable:
        def __deepcopy__(self, memo):
            server.tables[("dbo", "later")] = object()
            return "copied"

    server.tables[("dbo", "first")] = CreatingTable()
    server.snapshot()
    assert server._committed[0] == {("dbo", "first"): "copied"}
    assert ("dbo", "later") in server.tables
