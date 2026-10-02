"""Small builders the behavior tests share."""

from __future__ import annotations

from typing import Any

from shape_behavior import Module, Population, SimConfig, Simulator

T0 = "2020-01-01"


def module(states: dict[str, Any], attributes: dict[str, Any] | None = None, name: str = "t") -> Module:
    doc: dict[str, Any] = {"format": "shape-behavior/1", "name": name, "states": states}
    if attributes:
        doc["attributes"] = attributes
    return Module(doc)


def start(to: str = "a") -> dict[str, Any]:
    return {"type": "initial", "transition": {"direct": to}}


END: dict[str, Any] = {"type": "terminal"}


def run(
    modules: list[Module],
    size: int = 1000,
    until: str = "2030-01-01",
    seed: int = 1,
    **pop: Any,
) -> Any:
    sim = Simulator(modules, Population(size=size, start=T0, **pop), SimConfig(seed=seed))
    return sim.run_until(until)


def counts(table: Any, column: str = "state") -> dict[str, int]:
    out: dict[str, int] = {}
    for v in table.column(column).to_pylist():
        out[v] = out.get(v, 0) + 1
    return out
