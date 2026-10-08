"""Functional-dependency algorithms: closure, minimal cover, keys, 3NF synthesis, and the checks
that prove the synthesis guarantees (chase for lossless join, dependency preservation, 3NF).

Everything here is deterministic. Attribute sets are plain ``frozenset``s inside the algorithms,
but every result is ordered by each attribute's position in the schema (``attrs``), never by
set iteration, so the output does not depend on ``PYTHONHASHSEED``.

Stable interface: :class:`FD`, :class:`Relation`, :func:`closure`, :func:`minimal_cover`,
:func:`candidate_keys`, :func:`synthesize_3nf`, :func:`is_lossless`,
:func:`preserves_dependencies`, :func:`project_fds` and :func:`is_3nf`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from itertools import combinations

# Candidate-key search and FD projection are exponential in the number of attributes searched.
MAX_SEARCH_ATTRS = 22
MAX_PROJECT_ATTRS = 14


@dataclass(frozen=True, slots=True)
class FD:
    """``lhs -> rhs``: the attributes in ``lhs`` determine those in ``rhs``."""

    lhs: tuple[str, ...]
    rhs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Relation:
    """A relation scheme (attributes in schema order) and its candidate keys."""

    attrs: tuple[str, ...]
    keys: tuple[tuple[str, ...], ...]


def closure(attrs: Iterable[str], fds: Iterable[FD]) -> frozenset[str]:
    """Every attribute determined by ``attrs`` under ``fds`` (the attribute closure)."""
    result = set(attrs)
    deps = [(set(f.lhs), f.rhs) for f in fds]
    changed = True
    while changed:
        changed = False
        for lhs, rhs in deps:
            if lhs <= result and not set(rhs) <= result:
                result.update(rhs)
                changed = True
    return frozenset(result)


def _ordered(attrs: Iterable[str], order: Sequence[str]) -> tuple[str, ...]:
    wanted = set(attrs)
    return tuple(a for a in order if a in wanted)


def _check_attrs(order: Sequence[str], fds: Sequence[FD]) -> None:
    if len(set(order)) != len(order):
        raise ValueError("duplicate attribute in the schema")
    known = set(order)
    for f in fds:
        for a in (*f.lhs, *f.rhs):
            if a not in known:
                raise ValueError(f"unknown attribute {a!r} in dependency {f.lhs} -> {f.rhs}")
        if not f.lhs:
            raise ValueError("a dependency needs a non-empty determinant")


def minimal_cover(fds: Sequence[FD], order: Sequence[str]) -> list[FD]:
    """A minimal (canonical) cover: single-attribute right sides, no extraneous left attribute,
    no redundant dependency. Trivial dependencies are dropped. The result is sorted by schema
    position, so equal inputs give equal output."""
    _check_attrs(order, fds)
    pos = {a: i for i, a in enumerate(order)}
    work: list[tuple[tuple[str, ...], str]] = []
    for f in fds:
        lhs = _ordered(f.lhs, order)
        for a in _ordered(f.rhs, order):
            if a not in lhs and (lhs, a) not in work:
                work.append((lhs, a))

    def as_fds(items: Iterable[tuple[tuple[str, ...], str]]) -> list[FD]:
        return [FD(lhs, (a,)) for lhs, a in items]

    # 1. Remove extraneous left attributes.
    for i, (lhs, a) in enumerate(work):
        current = lhs
        for x in lhs:
            if len(current) == 1:
                break
            reduced = tuple(y for y in current if y != x)
            if a in closure(reduced, as_fds(work)):
                current = reduced
                work[i] = (current, a)
    # Left reduction can create duplicates.
    deduped: list[tuple[tuple[str, ...], str]] = []
    for item in work:
        if item not in deduped:
            deduped.append(item)
    work = deduped
    # 2. Remove redundant dependencies.
    i = 0
    while i < len(work):
        lhs, a = work[i]
        rest = work[:i] + work[i + 1 :]
        if a in closure(lhs, as_fds(rest)):
            work = rest
        else:
            i += 1
    work.sort(key=lambda item: ([pos[x] for x in item[0]], pos[item[1]]))
    return as_fds(work)


def candidate_keys(order: Sequence[str], fds: Sequence[FD]) -> list[tuple[str, ...]]:
    """Every candidate key (minimal superkey), each in schema order, shortest first, then by
    schema position."""
    _check_attrs(order, fds)
    everything = frozenset(order)
    rhs_attrs = {a for f in fds for a in f.rhs if a not in f.lhs}
    core = [a for a in order if a not in rhs_attrs]  # in every key: nothing determines them
    middle = [a for a in order if a in rhs_attrs]
    if closure(core, fds) >= everything:
        return [tuple(core)]
    if len(middle) > MAX_SEARCH_ATTRS:
        raise ValueError(
            f"candidate-key search over {len(middle)} attributes is too large "
            f"(limit {MAX_SEARCH_ATTRS}); declare the keys instead"
        )
    found: list[frozenset[str]] = []
    for size in range(1, len(middle) + 1):
        for extra in combinations(middle, size):
            cand = frozenset(core) | set(extra)
            if any(k <= cand for k in found):
                continue
            if closure(cand, fds) >= everything:
                found.append(cand)
    return sorted(
        (_ordered(k, order) for k in found),
        key=lambda k: (len(k), [order.index(a) for a in k]),
    )


def _is_subset_relation(a: tuple[str, ...], others: Iterable[tuple[str, ...]]) -> bool:
    return any(set(a) <= set(b) for b in others)


def project_fds(attrs: Sequence[str], fds: Sequence[FD]) -> list[FD]:
    """The dependencies of ``fds`` that hold within ``attrs`` (its projection), as a cover."""
    if len(attrs) > MAX_PROJECT_ATTRS:
        raise ValueError(
            f"projecting dependencies onto {len(attrs)} attributes is too large "
            f"(limit {MAX_PROJECT_ATTRS})"
        )
    out: list[FD] = []
    for size in range(1, len(attrs) + 1):
        for lhs in combinations(attrs, size):
            gained = closure(lhs, fds) & set(attrs) - set(lhs)
            if gained:
                out.append(FD(lhs, _ordered(gained, attrs)))
    return out


def is_3nf(attrs: Sequence[str], fds: Sequence[FD]) -> bool:
    """Whether the scheme ``attrs`` is in third normal form under ``fds`` (which must hold
    within ``attrs``; see :func:`project_fds`)."""
    keys = candidate_keys(list(attrs), fds)
    prime = {a for k in keys for a in k}
    for f in fds:
        determined = closure(f.lhs, fds)
        if set(attrs) <= determined:
            continue  # superkey
        for a in f.rhs:
            if a not in f.lhs and a not in prime:
                return False
    return True


def synthesize_3nf(order: Sequence[str], fds: Sequence[FD]) -> list[Relation]:
    """Bernstein's 3NF synthesis: a lossless-join, dependency-preserving decomposition.

    Minimal cover; one scheme per distinct left side (its attributes plus everything it
    determines in the cover); schemes inside another are dropped; a scheme holding a candidate
    key of the whole relation is added when none has one. Each scheme lists its candidate keys.
    """
    _check_attrs(order, fds)
    attrs = tuple(order)
    cover = minimal_cover(fds, attrs)
    groups: list[tuple[tuple[str, ...], set[str]]] = []
    for f in cover:
        for lhs, members in groups:
            if lhs == f.lhs:
                members.update(f.rhs)
                break
        else:
            groups.append((f.lhs, set(f.rhs)))
    schemes: list[tuple[str, ...]] = []
    for lhs, members in groups:
        scheme = _ordered(set(lhs) | members, attrs)
        schemes.append(scheme)
    # Drop schemes contained in another (first wins when two are equal).
    kept: list[tuple[str, ...]] = []
    for i, s in enumerate(schemes):
        inside = any(
            (set(s) < set(t)) or (set(s) == set(t) and j < i)
            for j, t in enumerate(schemes)
            if j != i
        )
        if not inside:
            kept.append(s)
    keys = candidate_keys(attrs, cover)
    if not any(set(k) <= set(s) for k in keys for s in kept):
        kept.append(keys[0])
    # A relation never shrinks the schema: attributes in no dependency live in every key, so the
    # key relation covers them; with no dependencies at all this is the whole relation.
    out: list[Relation] = []
    for s in kept:
        if len(s) <= MAX_PROJECT_ATTRS:
            rel_keys = candidate_keys(s, project_fds(s, cover))
        else:
            rel_keys = [lhs for lhs, _ in groups if set(lhs) <= set(s)][:1] or [s]
        out.append(Relation(s, tuple(rel_keys)))
    return out


def is_lossless(order: Sequence[str], schemes: Sequence[Sequence[str]], fds: Sequence[FD]) -> bool:
    """The chase test: whether joining ``schemes`` always gives back the relation, given ``fds``.

    Builds a tableau with one row per scheme (a distinguished symbol for each attribute the
    scheme has, a fresh symbol otherwise), applies the dependencies until nothing changes, and
    succeeds when some row is entirely distinguished.
    """
    _check_attrs(order, fds)
    col = {a: i for i, a in enumerate(order)}
    # symbol 0 is distinguished; fresh symbols are 1 + row * width + column, so they are unique.
    width = len(order)
    rows: list[list[int]] = []
    for r, scheme in enumerate(schemes):
        have = set(scheme)
        rows.append([0 if a in have else 1 + r * width + col[a] for a in order])
    changed = True
    while changed:
        changed = False
        for f in fds:
            lcols = [col[a] for a in f.lhs]
            groups: dict[tuple[int, ...], list[int]] = {}
            for i, row in enumerate(rows):
                groups.setdefault(tuple(row[c] for c in lcols), []).append(i)
            for members in groups.values():
                if len(members) < 2:
                    continue
                for a in f.rhs:
                    c = col[a]
                    target = min(rows[i][c] for i in members)
                    for i in members:
                        old = rows[i][c]
                        if old != target:
                            for row in rows:  # rename the symbol everywhere in the column
                                if row[c] == old:
                                    row[c] = target
                            changed = True
    return any(all(v == 0 for v in row) for row in rows)


def preserves_dependencies(schemes: Sequence[Sequence[str]], fds: Sequence[FD]) -> bool:
    """Whether every dependency in ``fds`` follows from the dependencies projected onto the
    ``schemes``, without computing the projections (the Beeri-Bernstein test)."""
    for f in fds:
        z = set(f.lhs)
        changed = True
        while changed:
            changed = False
            for s in schemes:
                gained = closure(z & set(s), fds) & set(s)
                if not gained <= z:
                    z |= gained
                    changed = True
        if not set(f.rhs) <= z:
            return False
    return True
