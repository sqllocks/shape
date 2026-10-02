"""Domains for the generation engine: the ``shape.domains`` plugins.

A domain is a plugin object with a ``name`` and ``definition()``, which returns a
``DomainDefinition``: a generation schema (the JSON document of ``generation-schema-v1.json``),
reference data tables and scale presets. :func:`load_domain` finds the plugin by name, registers
its reference data as the datasets that ``reference_data`` and ``record_sample`` read
(:func:`shape.generation.reference.register_dataset`) and returns the parsed schema.

Stable interface: ``load_domain``, ``LoadedDomain``, ``domain_names``, ``domain_modes``,
``DomainNotFoundError`` and ``DomainModeError``.
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


class DomainModeError(ShapeError, ValueError):
    """The domain has no schema in the mode asked for (``3nf`` or ``star``)."""


@dataclass(frozen=True, slots=True)
class LoadedDomain:
    """A domain ready to generate: its parsed schema and the definition it came from."""

    name: str
    schema: GenSchema
    definition: v1.DomainDefinition


def domain_names(host: PluginHost | None = None) -> list[str]:
    """The names of the installed domains (discovery only: nothing is imported)."""
    return (host or default_host()).names(GROUP)


def domain_modes(name: str, *, host: PluginHost | None = None) -> tuple[str, ...]:
    """The schema modes the domain ``name`` offers: its plugin's ``modes``, else ``("3nf",)``."""
    plugins = host or default_host()
    _require(plugins, name)
    return tuple(getattr(plugins.get(GROUP, name), "modes", ("3nf",)))


def _require(plugins: PluginHost, name: str) -> None:
    if name not in plugins.names(GROUP):
        known = ", ".join(plugins.names(GROUP)) or "none installed"
        raise DomainNotFoundError(
            f"no domain named {name!r} (installed: {known}); domains come from the "
            f"'sqllocks-shape-domains' package"
        )


def load_domain(
    name: str, *, mode: str | None = None, host: PluginHost | None = None
) -> LoadedDomain:
    """The domain ``name``: its schema, with its reference data registered for the strategies.

    ``mode`` (``3nf`` or ``star``) picks the schema of a domain that offers both: its plugin lists
    them in ``modes`` and takes the mode in ``definition(mode)``. Without ``mode`` the plugin's
    default schema is used. A mode the domain does not offer raises :class:`DomainModeError`."""
    plugins = host or default_host()
    _require(plugins, name)
    plugin = plugins.get(GROUP, name)
    if mode is None:
        definition: v1.DomainDefinition = plugin.definition()
    else:
        offered = tuple(getattr(plugin, "modes", ("3nf",)))
        if mode not in offered:
            raise DomainModeError(
                f"domain {name!r} has no {mode!r} mode (it offers: {', '.join(offered)})"
            )
        definition = plugin.definition(mode) if len(offered) > 1 else plugin.definition()
    for dataset, table in definition.reference_data.items():
        register_dataset(dataset, table)
    document: Any = definition.schema
    return LoadedDomain(name, GenSchema.from_dict(document), definition)


__all__ = [
    "DomainModeError",
    "DomainNotFoundError",
    "LoadedDomain",
    "domain_modes",
    "domain_names",
    "load_domain",
]
