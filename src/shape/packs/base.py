"""Versioned, provenance-aware domain Pack framework."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from shape.security import Sensitivity


@dataclass(frozen=True, slots=True)
class ReferenceAsset:
    name: str
    version: str
    source: str
    license_id: str
    sha256: str | None = None
    sensitivity: Sensitivity = field(default_factory=Sensitivity)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PackManifest:
    name: str
    version: str
    description: str
    assets: tuple[ReferenceAsset, ...] = ()
    offline_capable: bool = True
    required_capabilities: tuple[str, ...] = ()


class DomainPack:
    """A domain pack associated with its manifest."""

    def __init__(self, manifest: PackManifest):
        self.manifest = manifest
