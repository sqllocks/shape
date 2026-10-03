"""Chaos configuration: intensity presets, per-category weights, escalation and warmup."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChaosCategory(Enum):
    """The six categories of chaos that can be injected."""

    SCHEMA = "schema"
    VALUE = "value"
    FILE = "file"
    REFERENTIAL = "referential"
    TEMPORAL = "temporal"
    VOLUME = "volume"


# Each preset is a probability multiplier applied to every base injection probability.
INTENSITY_PRESETS: dict[str, float] = {
    "calm": 0.25,
    "moderate": 1.0,
    "stormy": 2.5,
    "hurricane": 5.0,
}

ESCALATIONS: tuple[str, ...] = ("gradual", "random", "front-loaded")

_DEFAULT_CATEGORIES: dict[str, dict[str, Any]] = {
    ChaosCategory.SCHEMA.value: {"enabled": True, "weight": 0.10},
    ChaosCategory.VALUE.value: {"enabled": True, "weight": 0.15},
    ChaosCategory.FILE.value: {"enabled": True, "weight": 0.08},
    ChaosCategory.REFERENTIAL.value: {"enabled": True, "weight": 0.10},
    ChaosCategory.TEMPORAL.value: {"enabled": True, "weight": 0.12},
    ChaosCategory.VOLUME.value: {"enabled": True, "weight": 0.08},
}


def _default_categories() -> dict[str, dict[str, Any]]:
    return copy.deepcopy(_DEFAULT_CATEGORIES)


@dataclass
class ChaosOverride:
    """Forces one chaos event of ``category`` on ``day``, bypassing the probability draw.

    ``params`` is kept for configuration compatibility and is not read: the mutators take no
    per-event parameters."""

    day: int
    category: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChaosConfig:
    """Top-level configuration of the chaos engine.

    ``enabled`` is the master switch (off: the engine never fires). ``intensity`` is a key of
    :data:`INTENSITY_PRESETS`. ``seed`` seeds the chaos RNG, which is independent of the
    generation seed. No chaos fires before ``chaos_start_day`` (the days before it are the
    warmup, so it must exceed ``warmup_days``). ``escalation`` shapes the probability over time:
    ``gradual`` ramps linearly from 0 to 1 over 30 chaos days, ``random`` draws uniformly each
    call, and ``front-loaded`` starts at 1 and decays by 5% a day (floor 0.1). ``categories``
    maps a category name to ``{"enabled": bool, "weight": float}``. Schema-breaking mutations
    (drop, rename, retype) are allowed only from ``breaking_change_day``.
    """

    enabled: bool = False
    intensity: str = "moderate"
    seed: int = 42
    warmup_days: int = 7
    chaos_start_day: int = 8
    escalation: str = "gradual"
    categories: dict[str, dict[str, Any]] = field(default_factory=_default_categories)
    overrides: list[ChaosOverride] = field(default_factory=list)
    breaking_change_day: int = 20

    @property
    def intensity_multiplier(self) -> float:
        """The numeric multiplier of the current intensity preset (1.0 when unknown)."""
        return INTENSITY_PRESETS.get(self.intensity, 1.0)

    def is_category_enabled(self, category: str) -> bool:
        return bool(self.categories.get(category, {}).get("enabled", False))

    def category_weight(self, category: str) -> float:
        """The base weight of a category (0.0 when missing or disabled)."""
        cat = self.categories.get(category, {})
        if not cat.get("enabled", False):
            return 0.0
        return float(cat.get("weight", 0.0))

    def overrides_for_day(self, day: int) -> list[ChaosOverride]:
        return [o for o in self.overrides if o.day == day]

    def validate(self) -> list[str]:
        """Error messages for an invalid configuration (an empty list means valid)."""
        errors: list[str] = []
        if self.intensity not in INTENSITY_PRESETS:
            errors.append(
                f"Unknown intensity '{self.intensity}'. Choose from: {', '.join(INTENSITY_PRESETS)}"
            )
        if self.escalation not in ESCALATIONS:
            errors.append(
                f"Unknown escalation '{self.escalation}'. Choose from: {', '.join(ESCALATIONS)}"
            )
        if self.chaos_start_day <= self.warmup_days:
            errors.append(
                f"chaos_start_day ({self.chaos_start_day}) must be > "
                f"warmup_days ({self.warmup_days})"
            )
        known = {c.value for c in ChaosCategory}
        for name, settings in self.categories.items():
            if name not in known:
                errors.append(f"Unknown category '{name}' in categories dict")
            if not isinstance(settings, dict):
                errors.append(
                    f"category '{name}' is a mapping like {{'enabled': true, 'weight': 0.1}}, "
                    f"got {settings!r}"
                )
                continue
            weight = settings.get("weight", 0.0)
            if (
                isinstance(weight, bool)
                or not isinstance(weight, (int, float))
                or not math.isfinite(weight)
                or weight < 0
            ):
                errors.append(f"category '{name}': weight is a number 0 or more, got {weight!r}")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int) or self.seed < 0:
            errors.append(f"seed is an integer 0 or more, got {self.seed!r}")
        for ov in self.overrides:
            if ov.category not in known:
                errors.append(
                    f"override on day {ov.day}: unknown category '{ov.category}'. "
                    f"Choose from: {', '.join(sorted(known))}"
                )
        return errors
