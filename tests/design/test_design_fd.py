"""W5-02 item 2: the textbook algorithms, checked against the guarantees directly."""

from __future__ import annotations

from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shape.design.fd import (
    FD,
    candidate_keys,
    closure,
    is_3nf,
    is_lossless,
    minimal_cover,
    preserves_dependencies,
    project_fds,
    synthesize_3nf,
)


def fds(*pairs: tuple[str, str]) -> list[FD]:
    return [FD(tuple(lhs.split()), tuple(rhs.split())) for lhs, rhs in pairs]


def as_set(cover: list[FD]) -> set[tuple[tuple[str, ...], tuple[str, ...]]]:
    return {(f.lhs, f.rhs) for f in cover}


def test_closure_follows_chains_and_ignores_unrelated() -> None:
    f = fds(("A", "B"), ("B", "C"), ("D", "E"))
    assert closure(["A"], f) == {"A", "B", "C"}
    assert closure(["D"], f) == {"D", "E"}
    assert closure([], f) == set()


def test_minimal_cover_textbook() -> None:
    # Elmasri and Navathe style: A->BC, B->C, A->B, AB->C reduces to A->B, B->C.
    f = fds(("A", "B C"), ("B", "C"), ("A", "B"), ("A B", "C"))
    assert as_set(minimal_cover(f, ["A", "B", "C"])) == {(("A",), ("B",)), (("B",), ("C",))}


def test_minimal_cover_left_reduces() -> None:
    # In {A->B, AB->C}, B is extraneous on the left of AB->C only if A->C is implied; here it is.
    f = fds(("A", "B"), ("A B", "C"))
    cover = minimal_cover(f, ["A", "B", "C"])
    assert as_set(cover) == {(("A",), ("B",)), (("A",), ("C",))}


def test_minimal_cover_has_singleton_rhs_and_is_equivalent() -> None:
    f = fds(("A", "B C D"), ("C", "D"))
    cover = minimal_cover(f, ["A", "B", "C", "D"])
    assert all(len(c.rhs) == 1 for c in cover)
    for original in f:
        assert set(original.rhs) <= closure(original.lhs, cover)
    for c in cover:
        assert set(c.rhs) <= closure(c.lhs, f)


def test_candidate_keys() -> None:
    f = fds(("A", "B"), ("B", "C"))
    assert candidate_keys(["A", "B", "C"], f) == [("A",)]
    assert candidate_keys(["A", "B", "C", "D"], f) == [("A", "D")]
    # Two keys: A <-> B.
    g = fds(("A", "B"), ("B", "A"), ("A", "C"))
    assert candidate_keys(["A", "B", "C"], g) == [("A",), ("B",)]
    # No dependencies: every attribute is in the key.
    assert candidate_keys(["A", "B"], []) == [("A", "B")]


def test_synthesis_chain() -> None:
    f = fds(("A", "B"), ("B", "C"))
    rels = synthesize_3nf(["A", "B", "C"], f)
    assert [r.attrs for r in rels] == [("A", "B"), ("B", "C")]
    assert rels[0].keys == (("A",),)
    assert rels[1].keys == (("B",),)


def test_synthesis_adds_key_relation_when_no_relation_holds_a_key() -> None:
    f = fds(("A", "B"))
    rels = synthesize_3nf(["A", "B", "C"], f)
    assert [r.attrs for r in rels] == [("A", "B"), ("A", "C")]
    assert rels[1].keys == (("A", "C"),)
    assert is_lossless(["A", "B", "C"], [r.attrs for r in rels], f)


def test_synthesis_keeps_3nf_but_not_bcnf_relation_whole() -> None:
    # street, city -> zip and zip -> city: the classic 3NF-not-BCNF relation stays one table
    # (the only decomposition that splits it loses street city -> zip).
    f = fds(("street city", "zip"), ("zip", "city"))
    rels = synthesize_3nf(["street", "city", "zip"], f)
    assert [r.attrs for r in rels] == [("street", "city", "zip")]
    assert rels[0].keys == (("street", "city"), ("street", "zip"))


def test_synthesis_with_no_dependencies_is_one_relation() -> None:
    rels = synthesize_3nf(["A", "B"], [])
    assert [r.attrs for r in rels] == [("A", "B")]
    assert rels[0].keys == (("A", "B"),)


def test_synthesis_rejects_unknown_attribute() -> None:
    with pytest.raises(ValueError, match="unknown attribute"):
        synthesize_3nf(["A"], fds(("A", "B")))


def test_chase_detects_a_lossy_decomposition() -> None:
    f = fds(("A", "B"))
    assert not is_lossless(["A", "B", "C", "D"], [("A", "B"), ("C", "D")], f)
    assert is_lossless(["A", "B", "C"], [("A", "B"), ("A", "C")], f)
    # Without the dependency the same split is lossy.
    assert not is_lossless(["A", "B", "C"], [("A", "B"), ("A", "C")], [])


def test_dependency_preservation_detects_a_loss() -> None:
    f = fds(("A", "B"), ("B", "C"))
    assert preserves_dependencies([("A", "B"), ("B", "C")], f)
    # {A,B},{A,C} is lossless but B->C is not preserved.
    assert not preserves_dependencies([("A", "B"), ("A", "C")], f)


def test_project_and_is_3nf() -> None:
    f = fds(("A", "B"), ("B", "C"))
    assert is_3nf(("A", "B", "C"), f) is False  # B->C: B is no key and C is non-prime
    assert is_3nf(("A", "B"), project_fds(("A", "B"), f))
    # 3NF but not BCNF.
    g = fds(("street city", "zip"), ("zip", "city"))
    assert is_3nf(("street", "city", "zip"), g)


# --- property-based: the guarantees hold for random inputs --------------------------------------

ATTRS = ["A", "B", "C", "D", "E", "F"]


@st.composite
def problems(draw: st.DrawFn) -> tuple[list[str], list[FD]]:
    n = draw(st.integers(2, 6))
    attrs = ATTRS[:n]
    deps = draw(
        st.lists(
            st.tuples(
                st.lists(st.sampled_from(attrs), min_size=1, max_size=3, unique=True),
                st.lists(st.sampled_from(attrs), min_size=1, max_size=2, unique=True),
            ),
            max_size=6,
        )
    )
    return attrs, [FD(tuple(sorted(a for a in lhs)), tuple(sorted(rhs))) for lhs, rhs in deps]


@settings(max_examples=150, deadline=None)
@given(problems())
def test_synthesis_guarantees_on_random_inputs(problem: tuple[list[str], list[FD]]) -> None:
    attrs, deps = problem
    rels = synthesize_3nf(attrs, deps)
    schemes = [r.attrs for r in rels]
    # Covers every attribute, no relation inside another.
    assert {a for s in schemes for a in s} == set(attrs)
    for s, t in combinations(schemes, 2):
        assert not set(s) <= set(t) and not set(t) <= set(s)
    # Lossless join (chase) and dependency preservation.
    assert is_lossless(attrs, schemes, deps)
    assert preserves_dependencies(schemes, deps)
    # Some relation holds a candidate key of the whole schema.
    keys = candidate_keys(attrs, deps)
    assert any(set(k) <= set(s) for k in keys for s in schemes)
    # Every relation is in 3NF under the dependencies projected onto it.
    for s in schemes:
        assert is_3nf(s, project_fds(s, deps))
    # Declared keys of each relation really are keys of it.
    for r in rels:
        projected = project_fds(r.attrs, deps)
        for k in r.keys:
            assert set(r.attrs) <= closure(k, projected)


@settings(max_examples=150, deadline=None)
@given(problems())
def test_minimal_cover_is_equivalent_and_irredundant(problem: tuple[list[str], list[FD]]) -> None:
    attrs, deps = problem
    cover = minimal_cover(deps, attrs)
    for d in deps:
        assert set(d.rhs) <= closure(d.lhs, cover)
    for c in cover:
        assert set(c.rhs) <= closure(c.lhs, deps)
        assert len(c.rhs) == 1
        rest = [x for x in cover if x is not c]
        assert not set(c.rhs) <= closure(c.lhs, rest)  # not redundant
        for a in c.lhs:  # no extraneous left attribute
            if len(c.lhs) > 1:
                reduced = tuple(x for x in c.lhs if x != a)
                assert not set(c.rhs) <= closure(reduced, cover)


@settings(max_examples=100, deadline=None)
@given(problems())
def test_synthesis_ignores_the_order_of_the_input_dependencies(
    problem: tuple[list[str], list[FD]],
) -> None:
    attrs, deps = problem
    a = synthesize_3nf(attrs, deps)
    b = synthesize_3nf(attrs, list(reversed(deps)))
    # The cover is not unique in general, so only the guarantees must agree, not the schemes;
    # for equal covers the output must be equal.
    if as_set(minimal_cover(deps, attrs)) == as_set(minimal_cover(list(reversed(deps)), attrs)):
        assert a == b
