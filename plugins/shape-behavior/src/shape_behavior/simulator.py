"""The simulator: modules run for a population on a virtual clock (``docs/plugins/behavior.md``).

State of a run is a few arrays: for every (entity, module) pair, the state to be entered next
(``state``), when (``next_time``, int64 microseconds) and how many states it has entered
(``step``). A round takes, for every entity with something due, its earliest pending pair
(ties: module order), enters that state for all such entities at once (grouped by module and
state, vectorized), and repeats until nothing is due before the horizon. Every random draw is
a pure function of ``(seed, entity id, module, step, draw index)``, which is what makes a run
independent of batching and exactly resumable.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

from shape_behavior import dist, rng
from shape_behavior.conditions import Compiled, code_of, compile_condition
from shape_behavior.events import EventBuffer
from shape_behavior.extension import Emission, StateContext, get_handler
from shape_behavior.model import EVENT_KINDS, Module, ModuleError, attribute_kind, load_module
from shape_behavior.population import INF, Population, Store, build_store
from shape_behavior.timeutil import from_us, parse_duration, to_us

_ZERO_LIMIT = 10_000  # consecutive zero-time entries of one module instance before we give up


@dataclass
class SimConfig:
    """``seed`` fixes every draw; ``poll`` is how often a guard on a non-age condition is re-tested."""

    seed: int = 0
    poll: str = "7 days"


# -- compiled transitions -----------------------------------------------------------------------


class _Direct:
    def __init__(self, target: int) -> None:
        self.target = target

    def choose(self, store: Store, rows: Any, t: Any, u: Any) -> Any:
        return np.full(len(rows), self.target, np.int32)


class _Distributed:
    def __init__(self, branches: list[dict[str, Any]], index: dict[str, int]) -> None:
        self.targets = np.array([index[b["to"]] for b in branches], np.int32)
        self.probs = [b["p"] for b in branches]
        self.constant = not any(isinstance(p, dict) for p in self.probs)
        if self.constant:
            w = np.array(self.probs, np.float64)
            self.cum = np.cumsum(w / w.sum())

    def choose(self, store: Store, rows: Any, t: Any, u: Any) -> Any:
        k = len(self.targets)
        if self.constant:
            pick = np.minimum(np.searchsorted(self.cum, u, side="right"), k - 1)
            return self.targets[pick]
        w = np.empty((len(rows), k))
        for j, p in enumerate(self.probs):
            if isinstance(p, dict):
                x = store.num[p["attribute"]][rows]
                w[:, j] = np.where(np.isnan(x), p["default"], x)
            else:
                w[:, j] = p
        total = w.sum(axis=1, keepdims=True)
        cum = np.cumsum(w / np.where(total > 0, total, 1.0), axis=1)
        pick = np.minimum((u[:, None] >= cum).sum(axis=1), k - 1)
        return self.targets[pick]


class _Branching:
    """``conditional`` and ``complex``: the first entry whose condition holds picks the target."""

    def __init__(self, entries: list[tuple[Compiled | None, Any]]) -> None:
        self.entries = entries

    def choose(self, store: Store, rows: Any, t: Any, u: Any) -> Any:
        out = np.full(len(rows), -2, np.int32)
        for cond, sub in self.entries:
            open_ = out == -2
            if not open_.any():
                break
            hit = open_ if cond is None else open_ & cond.test(store, rows, t)
            if hit.any():
                out[hit] = sub.choose(store, rows[hit], t[hit], u[hit])
        out[out == -2] = -1
        return out


def _compile_transition(
    t: dict[str, Any], index: dict[str, int], bit: Callable[[str, str], int]
) -> Any:
    (kind, body), = t.items()
    if kind == "stop":
        return _Direct(-1)
    if kind == "direct":
        return _Direct(index[body])
    if kind == "distributed":
        return _Distributed(body, index)
    entries: list[tuple[Compiled | None, Any]] = []
    for e in body:
        cond = compile_condition(e["if"], bit) if "if" in e else None
        sub = _Direct(index[e["to"]]) if "to" in e else _Distributed(e["distributed"], index)
        entries.append((cond, sub))
    return _Branching(entries)


# -- compiled modules ---------------------------------------------------------------------------


class _CState:
    def __init__(self, name: str, doc: dict[str, Any]) -> None:
        self.name = name
        self.doc = doc
        self.kind: str = doc["type"]
        self.trans: Any = None
        self.cond: Compiled | None = None
        self.poll_us = 0
        self.delay: dict[str, Any] | None = doc.get("delay")
        self.end_stop = self.kind in ("terminal", "death")


class _CModule:
    def __init__(self, module: Module, bit: Callable[[str, str], int], poll_us: int) -> None:
        self.module = module
        self.names = list(module.states)
        self.index = {n: i for i, n in enumerate(self.names)}
        self.initial = self.index[module.initial]
        self.states: list[_CState] = []
        for name in self.names:
            doc = module.states[name]
            cs = _CState(name, doc)
            if "transition" in doc:
                cs.trans = _compile_transition(doc["transition"], self.index, bit)
            if cs.kind == "guard":
                cs.cond = compile_condition(doc["condition"], bit)
                cs.poll_us = parse_duration(doc["poll"]) if "poll" in doc else poll_us
            self.states.append(cs)


class Checkpoint:
    """Everything needed to resume a run: arrays and a small metadata document (no pickles)."""

    def __init__(self, arrays: dict[str, Any], meta: dict[str, Any]) -> None:
        self.arrays = arrays
        self.meta = meta

    def save(self, path: str | Path) -> None:
        blob = np.frombuffer(json.dumps(self.meta, sort_keys=True).encode(), dtype=np.uint8)
        with open(path, "wb") as f:
            np.savez_compressed(f, __meta__=blob, **self.arrays)

    @classmethod
    def load(cls, path: str | Path) -> Checkpoint:
        with np.load(path, allow_pickle=False) as z:
            meta = json.loads(bytes(z["__meta__"]).decode())
            arrays = {k: z[k] for k in z.files if k != "__meta__"}
        return cls(arrays, meta)


class Simulator:
    """Runs ``modules`` for ``population``. See the module docstring and the docs page."""

    def __init__(
        self,
        modules: Sequence[Module | str | dict[str, Any] | Path],
        population: Population,
        config: SimConfig | None = None,
        *,
        _store: Store | None = None,
    ) -> None:
        self.config = config or SimConfig()
        self.seed = int(self.config.seed)
        self.population = population
        self.modules = [load_module(m) for m in modules]
        if not self.modules:
            raise ValueError("need at least one module")
        names = [m.name for m in self.modules]
        if len(set(names)) != len(names):
            raise ModuleError([f"module names must be unique, got {names}"])
        specs, kinds = self._attribute_plan()
        self._bits: dict[str, dict[str, int]] = {"condition": {}, "medication": {}}
        for m in self.modules:
            for which, codes in zip(("condition", "medication"), m.codes(), strict=True):
                for code in codes:
                    self._bits[which].setdefault(code, len(self._bits[which]))
        for which, table in self._bits.items():
            if len(table) > 64:
                raise ModuleError([f"more than 64 distinct {which} codes ({len(table)})"])
        poll_us = parse_duration(self.config.poll)
        self.cmods = [_CModule(m, self._bit, poll_us) for m in self.modules]
        self.store = _store or build_store(population, self.seed, specs, kinds)
        n, k = self.store.n, len(self.modules)
        self.state = np.zeros((n, k), np.int32)
        self.next_time = np.full((n, k), INF, np.int64)
        self.step = np.zeros((n, k), np.int64)
        self.zero_run = np.zeros((n, k), np.int32)
        for mi, cm in enumerate(self.cmods):
            self.state[:, mi] = cm.initial
            self.next_time[:, mi] = self.store.arrive
        self._clock = population.start_us - 1
        self._buffer = EventBuffer()

    # -- setup ---------------------------------------------------------------------------------

    def _bit(self, which: str, code: str) -> int:
        return self._bits[which][code]

    def _attribute_plan(self) -> tuple[dict[str, Any], dict[str, str]]:
        specs: dict[str, Any] = {}
        for m in self.modules:
            specs.update(m.doc.get("attributes", {}))
        specs.update(self.population.attributes)
        kinds: dict[str, str] = {}
        problems: list[str] = []
        used: dict[str, set[str]] = {}
        for m in self.modules:
            for name, ks in m.attribute_uses().items():
                used.setdefault(name, set()).update(ks)
        for name, spec in specs.items():
            k = attribute_kind(spec)
            if k:
                used.setdefault(name, set()).add(k)
        for name, ks in used.items():
            if len(ks) > 1:
                problems.append(f"attribute {name!r} is used both as a number and as text")
            kinds[name] = next(iter(ks)) if ks else "num"
        reserved = {"entity_id", "born", "arrival", "end"} & set(kinds)
        if reserved:
            problems.append(f"attribute names {sorted(reserved)} are reserved")
        if problems:
            raise ModuleError(problems)
        return specs, kinds

    # -- public --------------------------------------------------------------------------------

    @property
    def now(self) -> Any:
        """The virtual clock as a UTC ``datetime`` (the start time before the first run)."""
        return from_us(max(self._clock, self.population.start_us))

    def draw(self, mi: int, rows: Any, step: Any, k: int) -> Any:
        """Draw ``k`` of the module instance's step, for ``rows`` (internal and for handlers)."""
        return rng.uniform(self.seed, self.store.ids[rows], rng.stream_id(mi, step), k)

    def run_until(self, until: Any) -> Any:
        """Advance the clock to ``until`` and return the events in ``(clock, until]`` as an
        Arrow table (the first call also includes events at the start time itself)."""
        until_us = to_us(until)
        if until_us <= self._clock:
            return EventBuffer().table()
        self._buffer = EventBuffer()
        nt = self.next_time
        rows = np.flatnonzero(nt.min(axis=1) <= until_us)
        while rows.size:
            sub = nt[rows]
            m = sub.argmin(axis=1)
            t = sub[np.arange(len(rows)), m]
            ended = self.store.end[rows] <= t
            if ended.any():
                nt[rows[ended], :] = INF
            live = ~ended
            rows, m, t = rows[live], m[live], t[live]
            for mi in range(len(self.cmods)):
                sel = m == mi
                if sel.any():
                    self._enter_module(mi, rows[sel], t[sel])
            rows = rows[nt[rows].min(axis=1) <= until_us]
        self._emit_entity_ends(until_us)
        self._clock = until_us
        return self._buffer.table()

    def entities(self) -> Any:
        """One row per entity at the current clock: id, born, arrival, end and attributes."""
        s = self.store
        us = pa.timestamp("us")
        end = pa.array(s.end.astype("datetime64[us]"), us, mask=s.end == INF)
        cols: dict[str, Any] = {
            "entity_id": pa.array(s.ids, pa.int64()),
            "born": pa.array(s.born.astype("datetime64[us]"), us),
            "arrival": pa.array(s.arrive.astype("datetime64[us]"), us),
            "end": end,
        }
        allrows = np.arange(s.n)
        for name, arr in s.num.items():
            cols[name] = pa.array(arr, pa.float64(), mask=np.isnan(arr))
        for name in s.cat:
            cols[name] = pa.array(s.read(name, allrows).tolist(), pa.string())
        return pa.table(cols)

    def checkpoint(self) -> Checkpoint:
        s = self.store
        arrays: dict[str, Any] = {
            "state": self.state,
            "next_time": self.next_time,
            "step": self.step,
            "zero_run": self.zero_run,
            "ids": s.ids,
            "born": s.born,
            "arrive": s.arrive,
            "end": s.end,
            "end_reported": s.end_reported,
            "seq": s.seq,
            "cond_mask": s.cond_mask,
            "med_mask": s.med_mask,
        }
        num, cat = list(s.num), list(s.cat)
        for i, name in enumerate(num):
            arrays[f"num_{i}"] = s.num[name]
        for i, name in enumerate(cat):
            arrays[f"cat_{i}"] = s.cat[name]
        meta = {
            "version": 1,
            "seed": self.seed,
            "clock": int(self._clock),
            "poll": self.config.poll,
            "digests": [m.digest() for m in self.modules],
            "population": self.population.to_dict(),
            "num": num,
            "cat": cat,
            "vocab": {n: s.vocab[n] for n in cat},
        }
        return Checkpoint({k: np.array(v, copy=True) for k, v in arrays.items()}, meta)

    @classmethod
    def resume(
        cls, source: Checkpoint | str | Path, modules: Sequence[Module | str | dict[str, Any] | Path]
    ) -> Simulator:
        """Continue a checkpointed run. ``modules`` must be the modules it was started with."""
        ck = source if isinstance(source, Checkpoint) else Checkpoint.load(source)
        meta, a = ck.meta, ck.arrays
        mods = [load_module(m) for m in modules]
        if [m.digest() for m in mods] != meta["digests"]:
            raise ModuleError(["the modules differ from the ones the checkpoint was made with"])
        pop = Population.from_dict(meta["population"])
        sim = cls(mods, pop, SimConfig(seed=meta["seed"], poll=meta["poll"]), _store=_empty_store(a))
        s = sim.store
        for i, name in enumerate(meta["num"]):
            s.num[name] = a[f"num_{i}"].copy()
        for i, name in enumerate(meta["cat"]):
            s.cat[name] = a[f"cat_{i}"].copy()
            s.vocab[name] = list(meta["vocab"][name])
            s.vindex[name] = {v: j for j, v in enumerate(s.vocab[name])}
        s.end_reported, s.seq = a["end_reported"].copy(), a["seq"].copy()
        s.cond_mask, s.med_mask = a["cond_mask"].copy(), a["med_mask"].copy()
        sim.state, sim.next_time = a["state"].copy(), a["next_time"].copy()
        sim.step, sim.zero_run = a["step"].copy(), a["zero_run"].copy()
        sim._clock = int(meta["clock"])
        return sim

    # -- internals -----------------------------------------------------------------------------

    def _emit_entity_ends(self, until_us: int) -> None:
        s = self.store
        due = np.flatnonzero((s.end <= until_us) & ~s.end_reported)
        if due.size == 0:
            return
        s.end_reported[due] = True
        seq = s.seq[due].copy()
        s.seq[due] += 1
        self._buffer.add(s.ids[due], seq, s.end[due], {"module": "", "state": "", "kind": "entity_end"}, None)

    def _enter_module(self, mi: int, rows: Any, t: Any) -> None:
        cm = self.cmods[mi]
        states = self.state[rows, mi]
        steps = self.step[rows, mi]
        for si in np.unique(states):
            sel = states == si
            self._enter_state(mi, cm, cm.states[int(si)], rows[sel], t[sel], steps[sel])
        self.step[rows, mi] += 1

    def _enter_state(self, mi: int, cm: _CModule, cs: _CState, rows: Any, t: Any, steps: Any) -> None:
        store = self.store
        proceed = np.ones(len(rows), bool)
        wait_until = None
        if cs.cond is not None:  # guard
            proceed = cs.cond.test(store, rows, t)
            wait_until = t + cs.poll_us
            if cs.cond.ready is not None:
                ready = cs.cond.ready(store, rows)
                wait_until = np.where(ready > t, ready, wait_until)
        else:
            self._apply_effect(mi, cm, cs, rows, t, steps)
        if cs.end_stop:
            self.next_time[rows, mi] = INF
            return
        u = self.draw(mi, rows, steps, 0)
        nxt = cs.trans.choose(store, rows, t, u)
        new_t = t.copy()
        if cs.delay is not None:
            new_t = t + dist.sample_us(cs.delay, self.draw(mi, rows, steps, 1), self.draw(mi, rows, steps, 2))
        new_state = np.where(nxt >= 0, nxt, self.state[rows, mi])
        new_time = np.where(nxt >= 0, new_t, INF)
        if wait_until is not None:
            new_state = np.where(proceed, new_state, self.state[rows, mi])
            new_time = np.where(proceed, new_time, wait_until)
        self.state[rows, mi] = new_state
        self.next_time[rows, mi] = new_time
        same = (new_time == t) & proceed
        run = np.where(same, self.zero_run[rows, mi] + 1, 0)
        self.zero_run[rows, mi] = run
        if run.max(initial=0) > _ZERO_LIMIT:
            raise RuntimeError(
                f"module {cm.module.name!r}: state {cs.name!r} loops without time passing "
                f"(more than {_ZERO_LIMIT} entries at one instant); add a delay"
            )

    def _apply_effect(self, mi: int, cm: _CModule, cs: _CState, rows: Any, t: Any, steps: Any) -> None:
        store, doc, kind = self.store, cs.doc, cs.kind
        emission: dict[str, Any] = {"module": cm.module.name, "state": cs.name}
        value: Any = None
        emit = kind in EVENT_KINDS
        if kind == "set_attribute":
            name = doc["attribute"]
            if "distribution" in doc:
                v: Any = dist.sample(doc["distribution"], self.draw(mi, rows, steps, 3), self.draw(mi, rows, steps, 4))
            else:
                v = doc["value"]
                v = float(v) if isinstance(v, (bool, int, float)) else v
            store.write(name, rows, v)
        elif kind == "counter":
            name = doc["attribute"]
            sign = -1.0 if doc.get("action") == "decrement" else 1.0
            store.num[name][rows] = np.nan_to_num(store.num[name][rows]) + sign * float(doc.get("amount", 1))
        elif kind in ("condition_onset", "medication_order"):
            which = "condition" if kind == "condition_onset" else "medication"
            code = code_of(doc["codes"][0])
            self._set_bits(which, rows, 1 << self._bits[which][str(code)], on=True)
            if "assign_to_attribute" in doc:
                store.write(doc["assign_to_attribute"], rows, code)
            emission["ref"] = None
        elif kind in ("condition_end", "medication_end"):
            self._end_codes(kind, doc, cm, rows, emission)
        elif kind == "death":
            store.end[rows] = t
            store.end_reported[rows] = True
        elif kind == "observation":
            if "exact" in doc:
                value = float(doc["exact"])
            elif "range" in doc:
                r = doc["range"]
                value = r["low"] + (r["high"] - r["low"]) * self.draw(mi, rows, steps, 3)
            else:
                value = store.num[doc["attribute"]][rows]
        elif kind == "event":
            emission["text"] = store.read(doc["text_from"], rows) if "text_from" in doc else None
            if "value_from" in doc:
                value = store.num[doc["value_from"]][rows]
            elif "value" in doc:
                value = float(doc["value"])
            emit = True
        else:
            handler = get_handler(kind)
            if handler is not None and kind not in EVENT_KINDS:
                ctx = StateContext(doc, cm.module.name, rows, t, store.ids[rows], self, steps, mi)
                em = handler.apply(ctx)
                emit = em is not None
                if em is not None:
                    value = em.value
                    for col in ("kind", "code", "system", "display", "ref", "unit", "text", "payload"):
                        emission[col] = getattr(em, col)
        if not emit:
            return
        emission.setdefault("kind", EVENT_KINDS.get(kind, doc.get("event")))
        if emission["kind"] is None:
            emission["kind"] = doc.get("event", kind)
        if kind in EVENT_KINDS or kind == "event":
            first = (doc.get("codes") or [{}])[0]
            if isinstance(first, dict):
                for col, key in (("code", "code"), ("system", "system"), ("display", "display")):
                    emission.setdefault(col, first.get(key))
            if "payload" in doc:
                emission["payload"] = json.dumps(doc["payload"], sort_keys=True)
            if "unit" in doc:
                emission["unit"] = doc["unit"]
        seq = store.seq[rows].copy()
        store.seq[rows] += 1
        self._buffer.add(store.ids[rows], seq, t, emission, value)

    def _set_bits(self, which: str, rows: Any, mask: int, *, on: bool) -> None:
        arr = self.store.cond_mask if which == "condition" else self.store.med_mask
        m = np.uint64(mask)
        arr[rows] = (arr[rows] | m) if on else (arr[rows] & ~m)

    def _end_codes(self, kind: str, doc: dict[str, Any], cm: _CModule, rows: Any, emission: dict[str, Any]) -> None:
        which = "condition" if kind == "condition_end" else "medication"
        onset_key = "condition_onset" if which == "condition" else "medication_order"
        if onset_key in doc:
            onset = cm.module.states[doc[onset_key]]
            code = str(code_of(onset["codes"][0]))
            self._set_bits(which, rows, 1 << self._bits[which][code], on=False)
            emission["ref"] = doc[onset_key]
            emission["code"] = code
        elif "codes" in doc:
            mask = 0
            for c in doc["codes"]:
                mask |= 1 << self._bits[which][str(code_of(c))]
            self._set_bits(which, rows, mask, on=False)
            emission["code"] = str(code_of(doc["codes"][0]))
        else:
            values = self.store.read(doc["referenced_by_attribute"], rows)
            for v in {x for x in values.tolist() if x is not None}:
                bit = self._bits[which].get(str(v))
                if bit is not None:
                    self._set_bits(which, rows[values == v], 1 << bit, on=False)
            emission["code"] = values


def _empty_store(a: dict[str, Any]) -> Store:
    return Store(a["ids"].copy(), a["born"].copy(), a["arrive"].copy(), a["end"].copy())


__all__ = ["Checkpoint", "Emission", "SimConfig", "Simulator"]
