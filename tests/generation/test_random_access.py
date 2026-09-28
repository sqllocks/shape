from shape.generation import Choice, Empirical, GenerationPlan, SequenceStrategy, UniqueToken


def test_random_access_partition_determinism():
    p = GenerationPlan((("id", SequenceStrategy()), ("x", Choice(("a", "b", "c")))), seed=99)
    whole = list(p.rows(1000))
    parts = (
        list(p.rows_at(range(0, 137)))
        + list(p.rows_at(range(137, 777)))
        + list(p.rows_at(range(777, 1000)))
    )
    assert whole == parts


def test_advanced_strategies():
    p = GenerationPlan(
        (
            ("id", SequenceStrategy()),
            ("u", UniqueToken("X", 5)),
            ("e", Empirical(("a", "b"), (0.8, 1.0))),
        ),
        seed=2,
    )
    rows = list(p.rows(20))
    assert rows[0]["u"] == "X00000" and {r["e"] for r in rows} <= {"a", "b"}
