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
from .model import DEFAULT_KINDS as DEFAULT_KINDS
from .model import FORMAT as FORMAT
from .model import KINDS as KINDS
from .model import MAX_VERSION as MAX_VERSION
from .model import RULES_VERSION as RULES_VERSION
from .model import STALE as STALE
from .model import STATUSES as STATUSES
from .model import VERSION as VERSION
from .model import Decision as Decision
from .model import DecisionError as DecisionError
from .model import DecisionFile as DecisionFile
from .model import Entry as Entry
from .model import Proposal as Proposal
from .model import RuleConflictError as RuleConflictError
from .model import UpdateResult as UpdateResult
from .model import dump_contract as dump_contract
from .relationships import propose_relationships as propose_relationships
from .rules import propose_rules as propose_rules

__all__ = [
    "DEFAULT_KINDS",
    "FORMAT",
    "KINDS",
    "MAX_VERSION",
    "RULES_VERSION",
    "STALE",
    "STATUSES",
    "VERSION",
    "Decision",
    "DecisionError",
    "DecisionFile",
    "Entry",
    "Proposal",
    "RuleConflictError",
    "UpdateResult",
    "apply_decisions",
    "dump_contract",
    "propose",
    "propose_pii",
    "propose_relationships",
    "propose_rules",
    "propose_semantics",
]


def propose(
    profile: Any,
    data: DataSource | None = None,
    *,
    kinds: Iterable[str] = DEFAULT_KINDS,
    min_confidence: float = 0.5,
    decisions: DecisionFile | None = None,
) -> list[Proposal]:
    """The proposals of ``kinds`` for ``profile`` (and ``data``, which adds value evidence),
    the most confident first.

    ``rule`` (contract rules, see :func:`propose_rules`) is proposed only when asked for, and is the
    one kind that takes several profiles: pass a list. ``decisions`` tells it which columns are
    personal data and which rules are already accepted."""
    chosen = list(kinds)
    for k in chosen:
        if k not in KINDS:
            raise ValueError(f"unknown kind {k!r}: choose from {', '.join(KINDS)}")
    several = isinstance(profile, (list, tuple)) and len(profile) > 1
    if several and any(k != "rule" for k in chosen):
        raise ValueError(
            "several profiles are for rule proposals only (kinds=['rule']); "
            "the other kinds read one profile"
        )
    runs = {
        "relationship": propose_relationships,
        "pii": propose_pii,
        "semantic": propose_semantics,
    }
    one = profile[0] if isinstance(profile, (list, tuple)) and len(profile) == 1 else profile
    out: list[Proposal] = []
    for k in KINDS:
        if k not in chosen:
            continue
        if k == "rule":
            out.extend(
                propose_rules(profile, data, min_confidence=min_confidence, decisions=decisions)
            )
        else:
            out.extend(runs[k](one, data, min_confidence=min_confidence))
    return sorted(out, key=lambda p: (-p.confidence, p.id))
