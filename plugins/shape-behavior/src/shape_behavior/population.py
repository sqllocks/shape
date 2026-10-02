"""Who is simulated: the population description, and the entity arrays the simulator advances."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from shape_behavior import dist, rng
from shape_behavior.timeutil import to_us, unit_us

INF = np.iinfo(np.int64).max


@dataclass
class Population:
    """The entities of a run (``docs/plugins/behavior.md``, section 4).

    ``size`` entities with ids ``first_id`` upward, created at ``start`` (the clock origin).
    ``attributes`` maps an attribute name to a spec: ``{"kind": "categorical", "values":
    {"F": .5, "M": .5}}`` or a numeric distribution (``constant``, ``uniform``, ``normal``,
    ``lognormal``, ``exponential``, ``bernoulli``). ``age_at_start`` is a distribution in years
    (default unit) of the entity's age when it arrives; ``arrival`` is ``None`` (at ``start``),
    ``{"kind": "uniform", "days": N}`` or a duration distribution of the delay after ``start``;
    ``lifetime`` is a duration distribution from arrival to the end of the entity.
    """

    size: int
    start: Any = "2020-01-01"
    attributes: dict[str, Any] = field(default_factory=dict)
    age_at_start: dict[str, Any] | None = None
    arrival: dict[str, Any] | None = None
    lifetime: dict[str, Any] | None = None
    first_id: int = 0

    def __post_init__(self) -> None:
        problems = self.check()
        if problems:
            raise ValueError("invalid population:\n  " + "\n  ".join(problems))

    @property
    def start_us(self) -> int:
        return to_us(self.start)

    def check(self) -> list[str]:
        out = []
        if isinstance(self.size, bool) or not isinstance(self.size, int) or self.size < 0:
            out.append("size must be a non-negative integer")
        try:
            to_us(self.start)
        except (TypeError, ValueError) as exc:
            out.append(f"start: {exc}")
        from shape_behavior.model import _check_attribute_spec

        for name, spec in self.attributes.items():
            out += _check_attribute_spec(spec, f"attributes.{name}")
        if self.age_at_start is not None:
            out += dist.check(self.age_at_start, "age_at_start")
        if self.arrival is not None:
            out += dist.check(_arrival_spec(self.arrival), "arrival", duration=True)
        if self.lifetime is not None:
            out += dist.check(self.lifetime, "lifetime", duration=True)
        return out

    def to_dict(self) -> dict[str, Any]:
        return {
            "size": self.size,
            "start": str(self.start),
            "attributes": copy.deepcopy(self.attributes),
            "age_at_start": copy.deepcopy(self.age_at_start),
            "arrival": copy.deepcopy(self.arrival),
            "lifetime": copy.deepcopy(self.lifetime),
            "first_id": self.first_id,
        }

    @classmethod
    def from_dict(cls, doc: dict[str, Any], **override: Any) -> Population:
        """A population from a document (as written by ``--population-spec``)."""
        merged = {**doc, **override}
        unknown = set(merged) - {"size", "start", "attributes", "age_at_start", "arrival", "lifetime", "first_id"}
        if unknown:
            raise ValueError(f"unknown population settings: {', '.join(sorted(unknown))}")
        return cls(**merged)


def _arrival_spec(spec: dict[str, Any]) -> dict[str, Any]:
    if spec.get("kind") == "at_start":
        return {"kind": "exact", "value": 0, "unit": "days"}
    if spec.get("kind") == "uniform" and "days" in spec:
        return {"kind": "uniform", "low": 0, "high": spec["days"], "unit": "days"}
    return spec


class Store:
    """The arrays of every entity: ids, times, attributes and the active code sets."""

    def __init__(self, ids: Any, born: Any, arrive: Any, end: Any) -> None:
        self.n = len(ids)
        self.ids = ids
        self.born = born
        self.arrive = arrive
        self.end = end
        self.end_reported = np.zeros(self.n, bool)
        self.seq = np.zeros(self.n, np.int32)
        self.num: dict[str, Any] = {}
        self.cat: dict[str, Any] = {}
        self.vocab: dict[str, list[str]] = {}
        self.vindex: dict[str, dict[str, int]] = {}
        self.cond_mask = np.zeros(self.n, np.uint64)
        self.med_mask = np.zeros(self.n, np.uint64)

    def add_attribute(self, name: str, kind: str) -> None:
        if name in self.num or name in self.cat:
            return
        if kind == "num":
            self.num[name] = np.full(self.n, np.nan)
        else:
            self.cat[name] = np.full(self.n, -1, np.int32)
            self.vocab[name] = []
            self.vindex[name] = {}

    def intern(self, name: str, value: str) -> int:
        idx = self.vindex[name].get(value)
        if idx is None:
            idx = len(self.vocab[name])
            self.vocab[name].append(value)
            self.vindex[name][value] = idx
        return idx

    def is_nil(self, name: str, rows: Any) -> Any:
        if name in self.num:
            return np.isnan(self.num[name][rows])
        if name in self.cat:
            return self.cat[name][rows] < 0
        return np.ones(len(rows), bool)

    def read(self, name: str, rows: Any) -> Any:
        if name in self.num:
            return self.num[name][rows].copy()
        if name not in self.cat:
            raise KeyError(f"unknown attribute {name!r}")
        vocab = np.array([*self.vocab[name], None], dtype=object)
        return vocab[self.cat[name][rows]]  # code -1 indexes the trailing None

    def write(self, name: str, rows: Any, values: Any) -> None:
        if name in self.num:
            self.num[name][rows] = np.asarray(values, dtype=np.float64)
            return
        if name not in self.cat:
            raise KeyError(f"unknown attribute {name!r}")
        if values is None or isinstance(values, str):
            self.cat[name][rows] = -1 if values is None else self.intern(name, values)
            return
        arr = np.asarray(values, dtype=object)
        codes = np.empty(len(arr), np.int32)
        for v in {x for x in arr.tolist()}:
            codes[arr == v] = -1 if v is None else self.intern(name, str(v))
        self.cat[name][rows] = codes

    def age_years(self, rows: Any, time: Any) -> Any:
        return (time - self.born[rows]) / unit_us("years")


def build_store(
    pop: Population, seed: int, attr_specs: dict[str, Any], kinds: dict[str, str]
) -> Store:
    """Create the entities: ids, arrival, birth and end times, and initial attribute values.

    Every draw is keyed by ``(seed, entity id, attribute name)``, so any id range of a population
    gets the same values it would get inside a larger one.
    """
    ids = pop.first_id + np.arange(pop.size, dtype=np.int64)

    def draws(name: str) -> tuple[Any, Any]:
        stream = rng.stream_id(rng.INIT_MODULE, rng.name_key(name))
        return rng.uniform(seed, ids, stream, 0), rng.uniform(seed, ids, stream, 1)

    start = pop.start_us
    if pop.arrival is None:
        arrive = np.full(pop.size, start, np.int64)
    else:
        arrive = start + dist.sample_us(_arrival_spec(pop.arrival), *draws("__arrival__"))
    if pop.age_at_start is None:
        born = arrive.copy()
    else:
        spec = {"unit": "years", **pop.age_at_start}
        age = dist.sample(spec, *draws("__age__")) * unit_us(spec["unit"])
        born = arrive - np.rint(np.maximum(age, 0)).astype(np.int64)
    end = np.full(pop.size, INF, np.int64)
    if pop.lifetime is not None:
        end = arrive + dist.sample_us(pop.lifetime, *draws("__lifetime__"))
    store = Store(ids, born, arrive, end)
    seen: dict[int, str] = {}
    for name in sorted(set(kinds) | set(attr_specs)):
        key = rng.name_key(name)
        if seen.setdefault(key, name) != name:
            raise ValueError(f"attribute names {seen[key]!r} and {name!r} collide; rename one")
    for name, kind in kinds.items():
        store.add_attribute(name, kind)
    for name, spec in attr_specs.items():
        u1, u2 = draws(name)
        if spec.get("kind") == "categorical":
            values = list(spec["values"])
            w = np.array([spec["values"][v] for v in values], dtype=np.float64)
            cum = np.cumsum(w / w.sum())
            codes = np.minimum(np.searchsorted(cum, u1, side="right"), len(values) - 1)
            for v in values:
                store.intern(name, str(v))
            store.cat[name][:] = codes.astype(np.int32)
        elif isinstance(spec.get("value"), str):
            store.cat[name][:] = store.intern(name, spec["value"])
        else:
            store.num[name][:] = dist.sample(spec, u1, u2)
    return store
