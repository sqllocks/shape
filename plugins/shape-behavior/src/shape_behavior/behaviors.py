"""Behavior modules as ``shape.behaviors`` plugins (plugin API v1, ``Behavior`` protocol).

A :class:`ModuleBehavior` wraps one :class:`~shape_behavior.model.Module` so the engine, the
plugin host, ``shape plugins list`` and the conformance kit can all reach it by name::

    from shape_behavior.behaviors import behavior

    b = behavior({"format": "shape-behavior/1", "name": "my_process", "states": {...}})
    events = b.simulate(population=1000, seed=7, years=3)   # a pyarrow.Table

The built-in examples register under ``shape.behaviors`` through the classes at the bottom of
this file; a third-party package registers its own module the same way
(``docs/plugins/behavior.md``, section 10).
"""

from __future__ import annotations

from typing import Any

from shape_behavior.extension import get_handler
from shape_behavior.model import EVENT_KINDS, Module, load_module

SHAPE_API = "1.0"
VERSION = "1.0"

DEFAULT_POPULATION: dict[str, Any] = {
    "age_at_start": {"kind": "uniform", "low": 0, "high": 90},
    "attributes": {"gender": {"kind": "categorical", "values": {"F": 0.5, "M": 0.5}}},
}


def population_settings(modules: list[Module]) -> dict[str, Any]:
    """The population settings the modules ask for (``population_defaults``; the first module
    wins on a clash), or :data:`DEFAULT_POPULATION` when none asks."""
    import copy

    spec: dict[str, Any] = {}
    for m in reversed(modules):
        spec.update(m.doc.get("population_defaults", {}))
    return spec or copy.deepcopy(DEFAULT_POPULATION)


class ModuleBehavior:
    """One module as a ``shape.behaviors`` plugin: name, version, states, attributes, events."""

    def __init__(self, module: Module | dict[str, Any] | str, version: str = VERSION) -> None:
        self.module = load_module(module)
        self.name: str = self.module.name
        self.version: str = str(self.module.doc.get("version", version))
        self.states: list[str] = list(self.module.states)
        self.attributes: list[str] = sorted(self.module.attribute_uses())
        self.events: list[str] = _event_kinds(self.module)

    def simulate(self, population: int, seed: int, years: float) -> Any:
        """Events of ``population`` entities over ``years`` from 2020-01-01, deterministic per
        ``seed`` (an Arrow table in :data:`shape_behavior.EVENT_SCHEMA`)."""
        from shape_behavior.population import Population
        from shape_behavior.simulator import SimConfig, Simulator
        from shape_behavior.timeutil import add_years, to_us

        start = "2020-01-01"
        pop = Population.from_dict(population_settings([self.module]), size=population, start=start)
        sim = Simulator([self.module], pop, SimConfig(seed=seed))
        return sim.run_until(add_years(to_us(start), years))


def behavior(module: Module | dict[str, Any] | str, version: str = VERSION) -> ModuleBehavior:
    """A :class:`ModuleBehavior` for a module, a module document or a module file path."""
    return ModuleBehavior(module, version)


def _event_kinds(module: Module) -> list[str]:
    kinds: set[str] = set()
    for state in module.states.values():
        kind = state["type"]
        if kind == "event":
            kinds.add(str(state["event"]))
        elif kind in EVENT_KINDS:
            kinds.add(EVENT_KINDS[kind])
        else:  # a registered state type may declare the kinds it emits (optional `kinds`)
            declared = getattr(get_handler(kind), "kinds", None)
            if declared is not None:
                kinds.update(declared(state))
    return sorted(kinds)


class Subscription(ModuleBehavior):
    """Subscription lifecycle: free trial, conversion, monthly payments, pause and cancel."""

    def __init__(self) -> None:
        super().__init__("subscription")


class EquipmentMaintenance(ModuleBehavior):
    """Equipment maintenance: run, degrade, fail, repair, with scheduled inspections."""

    def __init__(self) -> None:
        super().__init__("equipment_maintenance")


class HealthcareScreening(ModuleBehavior):
    """A tiny healthcare example (screening, diagnosis, treatment), written from scratch."""

    def __init__(self) -> None:
        super().__init__("healthcare_screening")


class EventSequence(ModuleBehavior):
    """An ordered funnel of named events with a continue probability per step (defaults)."""

    def __init__(self) -> None:
        from shape_behavior.primitives import event_sequence

        super().__init__(event_sequence())


class TelemetrySeries(ModuleBehavior):
    """A regular reading per device with drift, noise, skipped and stuck readings (defaults)."""

    def __init__(self) -> None:
        from shape_behavior.primitives import telemetry_series

        super().__init__(telemetry_series())


class TransactionStream(ModuleBehavior):
    """Transactions as a Poisson process per entity, with refunds and reversals (defaults)."""

    def __init__(self) -> None:
        from shape_behavior.primitives import transaction_stream

        super().__init__(transaction_stream())


class FileArrival(ModuleBehavior):
    """A feed with an expected arrival per schedule slot: on time, late, missing, duplicated."""

    def __init__(self) -> None:
        from shape_behavior.primitives import file_arrival

        super().__init__(file_arrival())


class EntityLifecycle(ModuleBehavior):
    """Create, update and delete events per entity (change-data-capture style)."""

    def __init__(self) -> None:
        from shape_behavior.primitives import entity_lifecycle

        super().__init__(entity_lifecycle())
