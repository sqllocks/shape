from shape.generation.strategies import (
    Choice,
    Conditional,
    Constant,
    Derived,
    ForeignKey,
    GenerationPlan,
    SequenceStrategy,
)


def test_plan_deterministic_and_relational():
    plan = GenerationPlan(
        (
            ("id", SequenceStrategy(100)),
            ("tier", Choice(("a", "b"), (0.8, 0.2))),
            ("double", Derived(lambda r: r["id"] * 2)),
        ),
        seed=7,
    )
    a = list(plan.rows(100))
    b = list(plan.rows(100))
    assert a == b and a[0]["id"] == 100 and a[-1]["double"] == 398


def test_fk_and_conditional():
    plan = GenerationPlan(
        (
            ("id", SequenceStrategy()),
            ("fk", ForeignKey((10, 20))),
            ("x", Conditional(lambda r: r["fk"] == 10, Constant("A"), Constant("B"))),
        ),
        seed=2,
    )
    rows = list(plan.rows(20))
    assert all((r["fk"], r["x"]) in {(10, "A"), (20, "B")} for r in rows)
