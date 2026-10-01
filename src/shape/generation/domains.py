"""Domains for the generation engine: the ``shape.domains`` plugins.

A domain is a plugin object with a ``name`` and ``definition()``, which returns a
``DomainDefinition``: a generation schema (the JSON document of ``generation-schema-v1.json``),
reference data tables and scale presets. :func:`load_domain` finds the plugin by name, registers
its reference data as the datasets that ``reference_data`` and ``record_sample`` read
(:func:`shape.generation.reference.register_dataset`) and returns the parsed schema.

Stable interface: ``load_domain``, ``LoadedDomain``, ``domain_names`` and ``DomainNotFoundError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from shape.errors import ShapeError
from shape.generation.reference import register_dataset
from shape.generation.schema import GenSchema
from shape.plugins.api import v1
from shape.plugins.host import PluginHost, default_host

GROUP = "shape.domains"


class DomainNotFoundError(ShapeError, LookupError):
    """No installed ``shape.domains`` plugin has the name asked for."""


@dataclass(frozen=True, slots=True)
class LoadedDomain:
    """A domain ready to generate: its parsed schema and the definition it came from."""

    name: str
    schema: GenSchema
    definition: v1.DomainDefinition


def domain_names(host: PluginHost | None = None) -> list[str]:
    """The names of the installed domains (discovery only: nothing is imported)."""
    return (host or default_host()).names(GROUP)


def load_domain(name: str, *, host: PluginHost | None = None) -> LoadedDomain:
    """The domain ``name``: its schema, with its reference data registered for the strategies."""
    plugins = host or default_host()
    if name not in plugins.names(GROUP):
        known = ", ".join(plugins.names(GROUP)) or "none installed"
        raise DomainNotFoundError(
            f"no domain named {name!r} (installed: {known}); domains come from the "
            f"'sqllocks-shape-domains' package"
        )
    definition: v1.DomainDefinition = plugins.get(GROUP, name).definition()
    for dataset, table in definition.reference_data.items():
        register_dataset(dataset, table)
    document: Any = definition.schema
    return LoadedDomain(name, GenSchema.from_dict(document), definition)


__all__ = ["DomainNotFoundError", "LoadedDomain", "domain_names", "load_domain"]
