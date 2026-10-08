"""The one ordered information-classification taxonomy.

Every release, redaction and propagation decision ranks labels through this module. The
levels, lowest to highest, are ``PUBLIC < INTERNAL < CONFIDENTIAL < SECRET < TOP_SECRET``.
``SENSITIVE`` and ``PII`` are accepted aliases of ``CONFIDENTIAL``: they rank the same and
keep the label the caller gave them, so artifacts that already say ``PII`` keep saying it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

DEFAULT_LEVELS: tuple[str, ...] = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET", "TOP_SECRET")
ALIASES: dict[str, str] = {"SENSITIVE": "CONFIDENTIAL", "PII": "CONFIDENTIAL"}

# Rank of every accepted label (canonical names and aliases) in the default taxonomy.
LEVELS: dict[str, int] = {
    **{name: rank for rank, name in enumerate(DEFAULT_LEVELS)},
    **{alias: DEFAULT_LEVELS.index(target) for alias, target in ALIASES.items()},
}


@dataclass(frozen=True, slots=True)
class ClassificationTaxonomy:
    levels: tuple[str, ...] = DEFAULT_LEVELS
    aliases: tuple[tuple[str, str], ...] = tuple(ALIASES.items())

    def canonical(self, label: str) -> str:
        """The level a label stands for (case-insensitive); ``ValueError`` when unknown."""
        name = str(label).strip().upper()
        name = dict(self.aliases).get(name, name)
        if name not in self.levels:
            raise ValueError(f"unknown classification {label!r}")
        return name

    def rank(self, label: str) -> int:
        return self.levels.index(self.canonical(label))

    def join(self, *labels: str) -> str:
        """The most restrictive of ``labels`` (the lowest level when there are none)."""
        return max(labels, key=self.rank) if labels else self.levels[0]

    def permits(self, data_level: str, clearance: str) -> bool:
        return self.rank(clearance) >= self.rank(data_level)

    def at_least(self, label: str, floor: str) -> bool:
        """True when ``label`` is at or above ``floor``."""
        return self.rank(label) >= self.rank(floor)


DEFAULT_TAXONOMY = ClassificationTaxonomy()


def join_all(labels: Iterable[str]) -> str:
    return DEFAULT_TAXONOMY.join(*labels)
