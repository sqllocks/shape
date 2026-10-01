"""G2 (plan Appendix A): ``GenerationPlan.row_at`` with ``FirstPerParent`` must not depend on the
order rows are asked for. A row is the first of its parent exactly when no earlier row has the
same parent."""

from __future__ import annotations

from shape.generation import (
    Conditional,
    FirstPerParent,
    ForeignKey,
    GenerationPlan,
    SequenceStrategy,
)


def _plan() -> GenerationPlan:
    return GenerationPlan(
        (
            ("id", SequenceStrategy()),
            ("parent", ForeignKey((10, 20, 30, 40))),
            ("kind", FirstPerParent("parent", "first", "later")),
        ),
        seed=7,
    )


def _truth(n: int) -> list[str]:
    """What the label of each of the first ``n`` rows is, by definition."""
    p = _plan()
    seen: set[int] = set()
    out = []
    for i in range(n):
        parent = p.row_at(i)["parent"]
        out.append("later" if parent in seen else "first")
        seen.add(parent)
    return out


def test_row_at_in_any_order_gives_the_same_rows():
    n = 60
    truth = _truth(n)
    assert truth.count("first") == 4  # one per parent value, so the labels are not all equal
    forward = [_plan().row_at(i)["kind"] for i in range(n)]
    p = _plan()
    backward = [p.row_at(i)["kind"] for i in reversed(range(n))][::-1]
    shuffled_plan = _plan()
    order = [(i * 37) % n for i in range(n)]
    shuffled = {i: shuffled_plan.row_at(i)["kind"] for i in order}
    assert forward == truth
    assert backward == truth
    assert [shuffled[i] for i in range(n)] == truth


def test_rows_at_matches_row_at_for_any_index_order():
    p = _plan()
    wanted = [9, 3, 3, 40, 1, 0, 25]
    assert list(p.rows_at(wanted)) == [_plan().row_at(i) for i in wanted]


def test_rows_matches_row_at():
    assert list(_plan().rows(30)) == [_plan().row_at(i) for i in range(30)]


def test_first_per_parent_inside_a_conditional():
    inner = FirstPerParent("parent", "first", "later")
    fields = (
        ("parent", ForeignKey((1, 2))),
        ("kind", Conditional(lambda row: True, inner, inner)),
    )
    forward = [GenerationPlan(fields, seed=3).row_at(i)["kind"] for i in range(12)]
    p = GenerationPlan(fields, seed=3)
    backward = [p.row_at(i)["kind"] for i in reversed(range(12))][::-1]
    assert forward == backward
    assert forward.count("first") == 2 and forward.count("later") == 10
