from shape.artifact import MigrationRegistry


def test_migration_chain():
    r = MigrationRegistry()
    r.register(1, 2, "v2", lambda m, s: ({**m, "x": 1}, {**s, "a": 1}))
    r.register(2, 3, "v3", lambda m, s: ({**m, "y": 2}, {**s, "b": 2}))
    m, s, a = r.migrate({"format_version": 1}, {}, 3)
    assert m["format_version"] == 3 and s == {"a": 1, "b": 2} and a == ("v2", "v3")
