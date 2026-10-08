"""Shape plugin API v1: one Protocol per entry-point group (plan section 4.3).

Plugins are trusted, in-process code (D-09). Every hook takes and returns whole Arrow batches
or arrays, never single values or rows (section 4.2, rule 3). A plugin module declares
``SHAPE_API = "1.x"``; the host rejects any other major version.

A Protocol is satisfied structurally: a plugin does not import or subclass it. Each entry point
in a group names a *factory* (a class or a zero-argument callable) that returns an object
implementing the group's Protocol. Every object carries a ``name`` string, which is its
registry key within the group.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol, runtime_checkable

import pyarrow as pa  # type: ignore[import-untyped]

SHAPE_API = "1.0"

# Entry-point group name -> the Protocol (by class name) an object in that group implements.
GROUPS: dict[str, str] = {
    "shape.sources": "Source",
    "shape.sinks": "Sink",
    "shape.detectors": "SemanticDetector",
    "shape.fitters": "DistributionFitter",
    "shape.strategies": "Strategy",
    "shape.distributions": "Distribution",
    "shape.calendars": "Calendar",
    "shape.domains": "Domain",
    "shape.chaos": "ChaosMutator",
    "shape.emitters": "Emitter",
    "shape.stream_sources": "StreamSource",
    "shape.transforms": "Transform",
    "shape.commands": "Command",
    "shape.reports": "ReportFormat",
}


@dataclass(frozen=True, slots=True)
class Detection:
    """A semantic label for a column, with a confidence in [0, 1]."""

    label: str
    confidence: float


@dataclass(frozen=True, slots=True)
class FitResult:
    """A fitted distribution: family, parameters and the Kolmogorov-Smirnov statistic."""

    family: str
    params: Mapping[str, float]
    ks: float


@dataclass(frozen=True, slots=True)
class ChaosReport:
    """What a mutator changed in one batch."""

    mutator: str
    rows_affected: int
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamOffset:
    """An opaque, JSON-serialisable position in an external stream."""

    value: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class GenerationContext:
    """What a strategy or distribution needs to build one chunk, deterministically.

    ``seed`` is the run seed; ``table``, ``column`` and ``chunk`` key the RNG stream so the
    result does not depend on the chunk layout. ``row_start`` and ``n_rows`` locate the chunk.
    ``columns`` holds already-generated columns of the same chunk that this column may use.
    """

    seed: int
    table: str
    column: str
    chunk: int
    row_start: int
    n_rows: int
    columns: Mapping[str, pa.Array] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DomainDefinition:
    """What a domain contributes: schema, reference data, profiles and scale presets."""

    schema: Mapping[str, Any]
    reference_data: Mapping[str, pa.Table] = field(default_factory=dict)
    profiles: Mapping[str, Any] = field(default_factory=dict)
    scale_presets: Mapping[str, Mapping[str, int]] = field(default_factory=dict)


@runtime_checkable
class Source(Protocol):
    """A URI or scheme to a ``RecordBatch`` iterator."""

    name: str
    schemes: Sequence[str]

    def can_open(self, uri: str) -> bool: ...

    def schema(self, uri: str, **options: Any) -> pa.Schema: ...

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]: ...


@runtime_checkable
class Sink(Protocol):
    """``RecordBatch``es for one table to a destination."""

    name: str
    schemes: Sequence[str]

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        """Write every batch; return the number of rows written."""
        ...


@runtime_checkable
class SemanticDetector(Protocol):
    """An array to a ``(label, confidence)`` or ``None`` when nothing matches."""

    name: str

    def detect(self, values: pa.Array, column: str) -> Detection | None: ...


@runtime_checkable
class DistributionFitter(Protocol):
    """A sample array to a family, its parameters and the KS statistic."""

    name: str
    families: Sequence[str]

    def fit(self, sample: pa.Array) -> FitResult | None: ...


@runtime_checkable
class Strategy(Protocol):
    """A column spec and a context to an Arrow array for one chunk."""

    name: str

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array: ...


@runtime_checkable
class Distribution(Protocol):
    """Parameters and an RNG stream to an array."""

    name: str

    def sample(self, params: Mapping[str, float], ctx: GenerationContext) -> pa.Array: ...


@runtime_checkable
class Calendar(Protocol):
    """A date range to per-date lift factors (1.0 means no change)."""

    name: str

    def lift(self, start: date, end: date) -> pa.Array:
        """Float64 factors, one per day from ``start`` to ``end`` inclusive."""
        ...


@runtime_checkable
class Domain(Protocol):
    """A schema with reference data, profiles and scale presets."""

    name: str

    def definition(self) -> DomainDefinition: ...


@runtime_checkable
class ChaosMutator(Protocol):
    """A batch to a mutated batch plus a report. Deterministic given ``seed``."""

    name: str

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, ChaosReport]: ...


@runtime_checkable
class Emitter(Protocol):
    """Event batches to an external stream."""

    name: str
    schemes: Sequence[str]

    def emit(self, uri: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        """Send every batch; return the number of events sent."""
        ...


@runtime_checkable
class StreamSource(Protocol):
    """Offsets and batches from an external stream."""

    name: str
    schemes: Sequence[str]

    def read(
        self, uri: str, start: StreamOffset | None = None, **options: Any
    ) -> Iterator[tuple[StreamOffset, pa.RecordBatch]]:
        """Yield each batch with the offset just after it, so a checkpoint can resume."""
        ...


@runtime_checkable
class Transform(Protocol):
    """Tables to tables."""

    name: str

    def apply(self, tables: Mapping[str, pa.Table], **options: Any) -> dict[str, pa.Table]: ...


@runtime_checkable
class Command(Protocol):
    """A ``shape <name>`` subcommand.

    ``configure`` adds the command's arguments to its argparse sub-parser; ``run`` receives the
    parsed namespace and returns the process exit code.
    """

    name: str
    help: str

    def configure(self, parser: Any) -> None: ...

    def run(self, args: Any) -> int: ...


@runtime_checkable
class ReportFormat(Protocol):
    """A report to bytes."""

    name: str
    extension: str

    def render(self, report: Mapping[str, Any]) -> bytes: ...


PROTOCOLS: dict[str, type] = {
    "Source": Source,
    "Sink": Sink,
    "SemanticDetector": SemanticDetector,
    "DistributionFitter": DistributionFitter,
    "Strategy": Strategy,
    "Distribution": Distribution,
    "Calendar": Calendar,
    "Domain": Domain,
    "ChaosMutator": ChaosMutator,
    "Emitter": Emitter,
    "StreamSource": StreamSource,
    "Transform": Transform,
    "Command": Command,
    "ReportFormat": ReportFormat,
}

__all__ = [
    "GROUPS",
    "PROTOCOLS",
    "SHAPE_API",
    "Calendar",
    "ChaosMutator",
    "ChaosReport",
    "Command",
    "Detection",
    "Distribution",
    "DistributionFitter",
    "Domain",
    "DomainDefinition",
    "Emitter",
    "FitResult",
    "GenerationContext",
    "ReportFormat",
    "SemanticDetector",
    "Sink",
    "Source",
    "StreamOffset",
    "StreamSource",
    "Strategy",
    "Transform",
]
