from datetime import UTC, datetime, timedelta

from shape.generation.relational import (
    ParentChildSpec,
    generate_children,
    scd2_versions,
)


def test_relational():
    parents = [{"id": 1}, {"id": 2}]
    a = list(generate_children(parents, ParentChildSpec("id", "parent_id", 1, 3), 7))
    b = list(generate_children(parents, ParentChildSpec("id", "parent_id", 1, 3), 7))
    assert a == b and all(x["parent_id"] in {1, 2} for x in a)


def test_scd2():
    x = list(scd2_versions("a", datetime(2026, 1, 1, tzinfo=UTC), 3, timedelta(days=30)))
    assert x[-1]["is_current"] and x[-1]["valid_to"] is None and not x[0]["is_current"]
