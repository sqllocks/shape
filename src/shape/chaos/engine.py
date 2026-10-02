"""The chaos engine: decides *when* to inject and delegates *what* to the category mutators.

The engine owns the chaos RNG. Whether chaos fires on a day for a category depends on the master
switch, the warmup, per-category weights, the intensity preset and the escalation curve.

    cfg = ChaosConfig(enabled=True, intensity="stormy", seed=99)
    engine = ChaosEngine(cfg)
    if engine.should_inject(day=12, category="value"):
        table = engine.corrupt_values(table, day=12)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape.chaos.categories import (
    FileChaosMutator,
    MutationEvent,
    ReferentialChaosMutator,
    SchemaChaosMutator,
    TemporalChaosMutator,
    ValueChaosMutator,
    VolumeChaosMutator,
    column_indices,
)
from shape.chaos.categories import _is_datetime as _is_datetime
from shape.chaos.config import ChaosCategory, ChaosConfig


@dataclass(slots=True)
class ChaosResult:
    """What :meth:`ChaosEngine.apply_all` produced: the mutated ``table``, the mutated
    ``tables`` (the referential chaos result, or the input dict) and every ``events`` entry."""

    table: pa.Table
    tables: dict[str, pa.Table] | None = None
    events: list[MutationEvent] = field(default_factory=list)


class ChaosEngine:
    """Orchestrates chaos injection across the six categories.

    ``seed``, when given, overrides ``config.seed``.
    """

    def __init__(self, config: ChaosConfig | None = None, seed: int | None = None) -> None:
        self._config = config or ChaosConfig()
        self._rng = np.random.default_rng(seed if seed is not None else self._config.seed)
        self.schema = SchemaChaosMutator(breaking_change_day=self._config.breaking_change_day)
        self.value = ValueChaosMutator()
        self.file = FileChaosMutator()
        self.referential = ReferentialChaosMutator()
        self.temporal = TemporalChaosMutator()
        self.volume = VolumeChaosMutator()
        self.last_events: list[MutationEvent] = []

    @property
    def config(self) -> ChaosConfig:
        return self._config

    @property
    def rng(self) -> np.random.Generator:
        return self._rng

    # -- injection decision -------------------------------------------------------------------

    def should_inject(self, day: int, category: str) -> bool:
        """Whether chaos fires on ``day`` for ``category``.

        ``False`` when the engine is disabled, the day is inside the warmup or the category is
        off. An override scheduled for that day and category always fires. Otherwise one draw is
        compared with ``min(weight * intensity * escalation, 1)``.
        """
        cfg = self._config
        if not cfg.enabled or day < cfg.chaos_start_day or not cfg.is_category_enabled(category):
            return False
        for ov in cfg.overrides_for_day(day):
            if ov.category == category:
                return True
        probability = min(
            cfg.category_weight(category) * cfg.intensity_multiplier * self._escalation_factor(day),
            1.0,
        )
        return float(self._rng.random()) < probability

    def _escalation_factor(self, day: int) -> float:
        chaos_day = day - self._config.chaos_start_day
        if chaos_day < 0:
            return 0.0
        mode = self._config.escalation
        if mode == "gradual":
            return min(chaos_day / 30.0, 1.0)
        if mode == "random":
            return float(self._rng.random())
        if mode == "front-loaded":
            return max(0.95**chaos_day, 0.1)
        return 1.0

    # -- per-category entry points ------------------------------------------------------------

    def _run(
        self, result: tuple[object, list[MutationEvent]]
    ) -> tuple[object, list[MutationEvent]]:
        self.last_events = result[1]
        return result

    def corrupt_values(self, table: pa.Table, day: int) -> pa.Table:
        """Value chaos: nulls, out-of-range numbers, junk text, encoding damage, future dates and
        negated amounts."""
        out, _ = self._run(
            self.value.apply(table, day, self._rng, self._config.intensity_multiplier)
        )
        assert isinstance(out, pa.Table)
        return out

    def drift_schema(self, table: pa.Table, day: int) -> pa.Table:
        """Schema chaos: add, reorder, and (from ``breaking_change_day``) drop, rename or retype
        columns."""
        out, _ = self._run(
            self.schema.apply(table, day, self._rng, self._config.intensity_multiplier)
        )
        assert isinstance(out, pa.Table)
        return out

    def corrupt_file(self, file_bytes: bytes, day: int) -> bytes:
        """File chaos on raw bytes: truncation, byte damage, partial write, empty file, garbage
        header, swapped delimiters, poison JSON or a stray BOM."""
        out, _ = self._run(
            self.file.apply(file_bytes, day, self._rng, self._config.intensity_multiplier)
        )
        assert isinstance(out, bytes)
        return out

    def inject_referential_chaos(
        self, tables: dict[str, pa.Table], day: int
    ) -> dict[str, pa.Table]:
        """Referential chaos: orphan foreign keys or duplicate primary keys."""
        out, _ = self._run(
            self.referential.apply(tables, day, self._rng, self._config.intensity_multiplier)
        )
        assert isinstance(out, dict)
        return out

    def inject_temporal_chaos(self, table: pa.Table, date_columns: list[str], day: int) -> pa.Table:
        """Temporal chaos on ``date_columns``: late arrivals, swapped timestamps, timezone
        shifts and daylight-saving boundary values."""
        out, _ = self._run(
            self.temporal.apply(
                table,
                day,
                self._rng,
                self._config.intensity_multiplier,
                date_columns=date_columns,
            )
        )
        assert isinstance(out, pa.Table)
        return out

    def inject_volume_chaos(self, table: pa.Table, day: int) -> pa.Table:
        """Volume chaos: a spike, an empty batch or a single row."""
        out, _ = self._run(
            self.volume.apply(table, day, self._rng, self._config.intensity_multiplier)
        )
        assert isinstance(out, pa.Table)
        return out

    # -- all categories for one day -----------------------------------------------------------

    def apply_all(
        self,
        table: pa.Table,
        day: int,
        *,
        tables: dict[str, pa.Table] | None = None,
        date_columns: list[str] | None = None,
    ) -> ChaosResult:
        """Run the categories in the order schema, value, temporal, volume, referential, each
        where :meth:`should_inject` says so, and return everything that changed.

        Referential chaos needs ``tables`` and its outcome is returned in ``ChaosResult.tables``.
        """
        if not self._config.enabled:
            return ChaosResult(table, tables)
        events: list[MutationEvent] = []

        def note() -> None:
            events.extend(self.last_events)

        if self.should_inject(day, ChaosCategory.SCHEMA.value):
            table = self.drift_schema(table, day)
            note()
        if self.should_inject(day, ChaosCategory.VALUE.value):
            table = self.corrupt_values(table, day)
            note()
        if self.should_inject(day, ChaosCategory.TEMPORAL.value):
            cols = date_columns or [
                table.schema.field(i).name for i in column_indices(table, _is_datetime)
            ]
            if cols:
                table = self.inject_temporal_chaos(table, cols, day)
                note()
        if self.should_inject(day, ChaosCategory.VOLUME.value):
            table = self.inject_volume_chaos(table, day)
            note()
        if tables is not None and self.should_inject(day, ChaosCategory.REFERENTIAL.value):
            tables = self.inject_referential_chaos(tables, day)
            note()
        return ChaosResult(table, tables, events)
