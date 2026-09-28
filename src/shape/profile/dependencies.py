"""Bounded dependency evidence for categorical relationships."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class FunctionalDependencyEvidence:
    determinant: tuple[str, ...]
    dependent: str
    rows: int
    determinant_groups: int
    violating_groups: int
    confidence: float


def functional_dependency(
    rows: Iterable[Mapping[str, Any]], determinant: tuple[str, ...], dependent: str
) -> FunctionalDependencyEvidence:
    if not determinant:
        raise ValueError("determinant cannot be empty")
    groups = defaultdict(Counter)
    n = 0
    for r in rows:
        n += 1
        groups[tuple(r.get(k) for k in determinant)][r.get(dependent)] += 1
    violations = sum(len(v) > 1 for v in groups.values())
    # Row-weighted predictability: choose modal dependent value per determinant group.
    correct = sum(max(v.values()) for v in groups.values()) if groups else 0
    return FunctionalDependencyEvidence(
        determinant, dependent, n, len(groups), violations, (correct / n if n else 0.0)
    )


@dataclass(frozen=True, slots=True)
class CandidateKeyEvidence:
    fields: tuple[str, ...]
    rows: int
    distinct: int
    null_rows: int
    unique: bool


def candidate_key(
    rows: Iterable[Mapping[str, Any]], fields: tuple[str, ...]
) -> CandidateKeyEvidence:
    seen = set()
    n = nulls = 0
    for r in rows:
        n += 1
        key = tuple(r.get(k) for k in fields)
        if any(v is None for v in key):
            nulls += 1
        seen.add(key)
    return CandidateKeyEvidence(
        fields, n, len(seen), nulls, n > 0 and len(seen) == n and nulls == 0
    )
