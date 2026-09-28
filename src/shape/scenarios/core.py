"""Controlled Shape mutation and stress/scenario generation."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from shape.generation import generate_from_shape


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    changes: dict


def apply_scenario(shape, scenario):
    s = deepcopy(shape)
    for path, change in scenario.changes.items():
        parts = path.split(".")
        obj = s
        for p in parts[:-1]:
            obj = obj[p]
        key = parts[-1]
        old = obj.get(key)
        if isinstance(change, str) and change.endswith("%"):
            pct = float(change[:-1]) / 100
            obj[key] = float(old) * (1 + pct)
        else:
            obj[key] = change
    return s


def generate_scenario(shape, scenario, n, seed=0):
    mutated = apply_scenario(shape, scenario)
    data, report = generate_from_shape(mutated, n, seed)
    return mutated, data, report
