"""Disclosure-risk measurements for release decisions."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class KAnonymityResult:
    k: int
    groups: int
    unique_groups: int
    risky_rows: int
    total_rows: int


def k_anonymity(
    rows: Iterable[Mapping[str, Any]], quasi_identifiers: tuple[str, ...]
) -> KAnonymityResult:
    """Measure equivalence-group counts for the selected fields; this is not a sharing approval."""
    if not quasi_identifiers:
        raise ValueError("quasi_identifiers cannot be empty")
    counts = Counter(tuple(r.get(k) for k in quasi_identifiers) for r in rows)
    if not counts:
        return KAnonymityResult(0, 0, 0, 0, 0)
    total = sum(counts.values())
    k = min(counts.values())
    return KAnonymityResult(
        k,
        len(counts),
        sum(v == 1 for v in counts.values()),
        sum(v for v in counts.values() if v < 5),
        total,
    )


@dataclass(frozen=True, slots=True)
class LDiversityResult:
    minimum_l: int
    violating_groups: int
    groups: int


def l_diversity(
    rows: Iterable[Mapping[str, Any]],
    quasi_identifiers: tuple[str, ...],
    sensitive_field: str,
    required_l: int = 2,
) -> LDiversityResult:
    """Measure distinct sensitive values within each selected equivalence group."""
    groups = defaultdict(set)
    for r in rows:
        groups[tuple(r.get(k) for k in quasi_identifiers)].add(r.get(sensitive_field))
    if not groups:
        return LDiversityResult(0, 0, 0)
    ls = [len(v) for v in groups.values()]
    return LDiversityResult(min(ls), sum(v < required_l for v in ls), len(ls))
