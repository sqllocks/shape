"""Bounded dependency evidence for categorical relationships."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from shape.kernel.values import DistinctCounter


@dataclass(frozen=True, slots=True)
class FunctionalDependencyEvidence:
    determinant: tuple[str, ...]
    dependent: str
    rows: int
    determinant_groups: int
    violating_groups: int
    confidence: float
    truncated: bool = False  # more than max_groups determinant values: later groups untracked
    rows_untracked: int = 0


def functional_dependency(
    rows: Iterable[Mapping[str, Any]],
    determinant: tuple[str, ...],
    dependent: str,
    max_groups: int = 100_000,
) -> FunctionalDependencyEvidence:
    """Row-weighted predictability of ``dependent`` from ``determinant``.

    Memory is bounded by ``max_groups`` determinant values: once that many groups exist, rows
    of further groups are counted in ``rows_untracked`` and the confidence is computed over
    the tracked rows (``truncated`` says so)."""
    if not determinant:
        raise ValueError("determinant cannot be empty")
    if max_groups < 1:
        raise ValueError("max_groups must be positive")
    groups: defaultdict[tuple[Any, ...], Counter[Any]] = defaultdict(Counter)
    n = untracked = 0
    for r in rows:
        n += 1
        key = tuple(r.get(k) for k in determinant)
        if key not in groups and len(groups) >= max_groups:
            untracked += 1
            continue
        groups[key][r.get(dependent)] += 1
    violations = sum(len(v) > 1 for v in groups.values())
    # Row-weighted predictability: choose modal dependent value per determinant group.
    correct = sum(max(v.values()) for v in groups.values()) if groups else 0
    tracked = n - untracked
    return FunctionalDependencyEvidence(
        determinant,
        dependent,
        n,
        len(groups),
        violations,
        (correct / tracked if tracked else 0.0),
        untracked > 0,
        untracked,
    )


@dataclass(frozen=True, slots=True)
class CandidateKeyEvidence:
    fields: tuple[str, ...]
    rows: int
    distinct: int
    null_rows: int
    unique: bool
    exact: bool = True  # False once more than max_keys distinct keys were seen


def candidate_key(
    rows: Iterable[Mapping[str, Any]], fields: tuple[str, ...], max_keys: int = 1_000_000
) -> CandidateKeyEvidence:
    """Whether ``fields`` are unique. Distinct keys are held exactly up to ``max_keys``; beyond
    that the count is a HyperLogLog estimate, ``exact`` is False, and uniqueness is not
    claimed (it cannot be proved from an estimate)."""
    if max_keys < 1:
        raise ValueError("max_keys must be positive")
    seen: set[tuple[Any, ...]] | None = set()
    hll: DistinctCounter | None = None
    n = nulls = 0
    for r in rows:
        n += 1
        key = tuple(r.get(k) for k in fields)
        if any(v is None for v in key):
            nulls += 1
        if seen is not None:
            seen.add(key)
            if len(seen) > max_keys:
                from shape.kernel.values import DistinctCounter

                hll = DistinctCounter()
                for k in seen:
                    hll.update(repr(k))
                seen = None
        else:
            assert hll is not None
            hll.update(repr(key))
    if seen is not None:
        return CandidateKeyEvidence(
            fields, n, len(seen), nulls, n > 0 and len(seen) == n and nulls == 0
        )
    assert hll is not None
    return CandidateKeyEvidence(fields, n, round(hll.estimate()), nulls, False, False)
