"""Proposals and decision files: Shape proposes, a person decides, and the answer is kept.

Some facts about a dataset cannot be settled by a profile alone: whether a column is a foreign key
to another table, whether it holds personal data, what it means. :func:`propose` finds candidates
with the evidence and a confidence; a :class:`DecisionFile` records a person's accept, reject or
defer (who, when, why); :func:`apply_decisions` and ``fit_schema(..., decisions=...)`` apply what
was decided; and a rejected proposal is never proposed again. Nothing is accepted automatically
unless an auto-accept threshold is given. See ``docs/PROPOSALS.md``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ._data import DataSource
from .apply import apply_decisions as apply_decisions
from .columns import propose_pii as propose_pii
from .columns import propose_semantics as propose_semantics
from .model import FORMAT as FORMAT
from .model import KINDS as KINDS
from .model import STATUSES as STATUSES
from .model import VERSION as VERSION
from .model import Decision as Decision
from .model import DecisionError as DecisionError
from .model import DecisionFile as DecisionFile
from .model import Entry as Entry
from .model import Proposal as Proposal
from .model import UpdateResult as UpdateResult
from .relationships import propose_relationships as propose_relationships

__all__ = [
    "FORMAT",
    "KINDS",
    "STATUSES",
    "VERSION",
    "Decision",
    "DecisionError",
    "DecisionFile",
    "Entry",
    "Proposal",
    "UpdateResult",
    "apply_decisions",
    "propose",
    "propose_pii",
    "propose_relationships",
    "propose_semantics",
]


def propose(
    profile: Any,
    data: DataSource | None = None,
    *,
    kinds: Iterable[str] = KINDS,
    min_confidence: float = 0.5,
) -> list[Proposal]:
    """The proposals of ``kinds`` for ``profile`` (and ``data``, which adds value evidence),
    the most confident first."""
    chosen = list(kinds)
    for k in chosen:
        if k not in KINDS:
            raise ValueError(f"unknown kind {k!r}: choose from {', '.join(KINDS)}")
    runs = {
        "relationship": propose_relationships,
        "pii": propose_pii,
        "semantic": propose_semantics,
    }
    out: list[Proposal] = []
    for k in KINDS:
        if k in chosen:
            out.extend(runs[k](profile, data, min_confidence=min_confidence))
    return sorted(out, key=lambda p: (-p.confidence, p.id))
