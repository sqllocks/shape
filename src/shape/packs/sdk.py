"""Minimal Pack authoring SDK."""

from __future__ import annotations

from .base import DomainPack, PackManifest


class PackBuilder:
    def __init__(self, name, version, description=""):
        self.manifest = PackManifest(name, version, description)
        self._generators = {}
        self._detectors = {}

    def generator(self, name, fn):
        self._generators[name] = fn
        return self

    def detector(self, name, fn):
        self._detectors[name] = fn
        return self

    def build(self):
        p = DomainPack(self.manifest)
        p.generators = dict(self._generators)
        p.detectors = dict(self._detectors)
        return p
