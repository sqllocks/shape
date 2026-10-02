"""Chaos engineering for generated data: deterministic data-quality fault injection.

Six categories (schema, value, file, referential, temporal and volume) live in
:mod:`shape.chaos.categories`; :class:`ChaosEngine` decides when each fires. For row-level
anomalies in a stream or a batch (``--anomaly-fraction``) call :func:`inject_anomalies`.
"""

from shape.chaos.anomaly import KINDS, AnomalyResult, inject_anomalies
from shape.chaos.categories import (
    FileChaosMutator,
    MutationEvent,
    Mutator,
    ReferentialChaosMutator,
    SchemaChaosMutator,
    TemporalChaosMutator,
    ValueChaosMutator,
    VolumeChaosMutator,
)
from shape.chaos.config import INTENSITY_PRESETS, ChaosCategory, ChaosConfig, ChaosOverride
from shape.chaos.engine import ChaosEngine, ChaosResult

__all__ = [
    "INTENSITY_PRESETS",
    "KINDS",
    "AnomalyResult",
    "ChaosCategory",
    "ChaosConfig",
    "ChaosEngine",
    "ChaosOverride",
    "ChaosResult",
    "FileChaosMutator",
    "MutationEvent",
    "Mutator",
    "ReferentialChaosMutator",
    "SchemaChaosMutator",
    "TemporalChaosMutator",
    "ValueChaosMutator",
    "VolumeChaosMutator",
    "inject_anomalies",
]
