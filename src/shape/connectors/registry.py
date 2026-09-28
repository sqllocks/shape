"""Connector factory registry with explicit capability names."""

from __future__ import annotations


class ConnectorRegistry:
    def __init__(self):
        self._factories = {}

    def register(self, name, factory):
        if not name or name in self._factories:
            raise ValueError("connector name empty or already registered")
        self._factories[name] = factory

    def create(self, name, **kwargs):
        if name not in self._factories:
            raise KeyError(f"unknown connector: {name}")
        return self._factories[name](**kwargs)

    @property
    def names(self):
        return tuple(sorted(self._factories))
