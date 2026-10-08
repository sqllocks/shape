"""Immutable Shape kernel model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid4

from shape.security import Sensitivity
from shape.types import FieldType


class Provenance(str, Enum):  # noqa: UP042 - preserve public enum string representation
    OBSERVED = "OBSERVED"
    INFERRED = "INFERRED"
    DECLARED = "DECLARED"
    DERIVED = "DERIVED"
    INTERPOLATED = "INTERPOLATED"
    EXTRAPOLATED = "EXTRAPOLATED"


@dataclass(frozen=True, slots=True)
class Evidence:
    provenance: Provenance
    value: Any
    method: str | None = None


@dataclass(frozen=True, slots=True)
class Shape:
    shape_id: UUID
    fields: tuple[FieldType, ...]
    sensitivity: Sensitivity = field(default_factory=Sensitivity)
    evidence: Mapping[str, Evidence] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))


class ShapeBuilder:
    """Mutable construction boundary; finalize() produces an immutable Shape."""

    def __init__(self) -> None:
        self._fields: list[FieldType] = []
        self._sensitivity = Sensitivity()
        self._evidence: dict[str, Evidence] = {}

    def add_field(self, field_type: FieldType) -> ShapeBuilder:
        self._fields.append(field_type)
        return self

    def add_evidence(self, key: str, evidence: Evidence) -> ShapeBuilder:
        self._evidence[key] = evidence
        return self

    def join_sensitivity(self, sensitivity: Sensitivity) -> ShapeBuilder:
        self._sensitivity = self._sensitivity.join(sensitivity)
        return self

    def finalize(self) -> Shape:
        return Shape(
            shape_id=uuid4(),
            fields=tuple(self._fields),
            sensitivity=self._sensitivity,
            evidence=dict(self._evidence),
        )
