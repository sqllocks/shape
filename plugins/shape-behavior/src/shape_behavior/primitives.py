"""The five behavior primitives, as parameterised module builders (``docs/plugins/behavior.md``,
section 11).

Each builder takes keyword parameters (every one has a default, so the call with no argument
runs) and returns a validated :class:`~shape_behavior.model.Module` in format
``shape-behavior/1``::

    from shape_behavior.primitives import telemetry_series
    from shape_behavior.behaviors import behavior

    events = behavior(telemetry_series(interval="1 hour", missing_rate=0.02)).simulate(100, 7, 1)

An invalid parameter raises :class:`~shape_behavior.model.ModuleError` naming it. The module
document records the effective parameters under ``"parameters"`` (and the primitive's name
under ``"primitive"``), so a run manifest can say what was used. Everything is deterministic:
random numbers come only from the engine's counter-based draws.

Four of the primitives register one state type each (``telemetry_reading``, ``transaction_event``,
``file_event``, ``lifecycle_event``) through the engine's extension point; importing this module
registers them.
"""

from __future__ import annotations

import inspect
import math
from collections.abc import Callable
from typing import Any

import numpy as np

from shape_behavior import dist
from shape_behavior.extension import Emission, StateContext, register_state_type
from shape_behavior.model import Module, ModuleError
from shape_behavior.params import (
    PARAMS_FORMAT,
    PARAMS_VERSION,
    PrimitiveParamsError,
    unknown_primitive,
)
from shape_behavior.timeutil import UNIT_US, US, parse_duration

SHAPE_API = "1.0"
VERSION = "1.0"


# -- parameter checks ---------------------------------------------------------------------------


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


class _Check:
    """Collects the problems of one primitive's parameters."""

    def __init__(self, primitive: str) -> None:
        self.primitive = primitive
        self.problems: list[str] = []

    def fail(self, param: str, what: str, got: Any = None, *, show: bool = True) -> None:
        text = f"{self.primitive}: {param} {what}"
        self.problems.append(text + (f", got {got!r}" if show else ""))

    def number(self, param: str, v: Any) -> float:
        if not _is_number(v):
            self.fail(param, "must be a finite number", v)
            return 0.0
        return float(v)

    def non_negative(self, param: str, v: Any, *, positive: bool = False) -> float:
        ok = _is_number(v) and (v > 0 if positive else v >= 0)
        if not ok:
            self.fail(param, f"must be a {'positive' if positive else 'non-negative'} number", v)
            return 1.0
        return float(v)

    def probability(self, param: str, v: Any) -> float:
        if not _is_number(v) or not 0 <= v <= 1:
            self.fail(param, "must be between 0 and 1", v)
            return 0.0
        return float(v)

    def duration(self, param: str, v: Any, *, positive: bool = True) -> int:
        """The duration in microseconds (0 when invalid; a problem is recorded)."""
        kind = "a positive duration" if positive else "a non-negative duration"
        if not isinstance(v, str):
            self.fail(param, f"must be {kind}", v)
            return 0
        try:
            us = parse_duration(v)
        except ValueError:
            self.fail(param, f"must be {kind}", v)
            return 0
        if us <= 0 if positive else us < 0:
            self.fail(param, f"must be {kind}", v)
            return 0
        return us

    def names(self, param: str, v: Any) -> list[str]:
        if not isinstance(v, list) or not v:
            self.fail(param, "must be a list of at least one name", v)
            return []
        if not all(isinstance(x, str) and x for x in v):
            self.fail(param, "must be non-empty strings", v)
            return []
        for x in v:
            if v.count(x) > 1:
                self.fail(param, f"must be unique, got {x!r} twice", show=False)
                return []
        return list(v)

    def raise_if_any(self) -> None:
        if self.problems:
            raise ModuleError(self.problems, self.primitive)


def _seconds(us: int) -> float:
    """Microseconds as the seconds value of an ``exact`` duration (exact for whole microseconds)."""
    return us / US


def _exact_delay(us: int) -> dict[str, Any]:
    return {"kind": "exact", "value": _seconds(us), "unit": "seconds"}


def _exponential_years(rate_per_year: float) -> dict[str, Any]:
    return {"kind": "exponential", "mean": 1.0 / rate_per_year, "unit": "years"}


def _module(
    name: str,
    parameters: dict[str, Any],
    states: dict[str, Any],
    population: dict[str, Any],
    note: str,
) -> Module:
    return Module(
        {
            "format": "shape-behavior/1",
            "name": name,
            "version": VERSION,
            "primitive": name,
            "parameters": parameters,
            "remarks": [note],
            "population_defaults": population,
            "states": states,
        }
    )


_AT_START: dict[str, Any] = {"arrival": {"kind": "at_start"}}
_SPREAD: dict[str, Any] = {"arrival": {"kind": "uniform", "days": 365}}


# -- event_sequence -----------------------------------------------------------------------------


def event_sequence(
    steps: list[str] | None = None,
    dropout: float | list[float] = 0.4,
    gap: str = "2 minutes",
) -> Module:
    """An ordered funnel of named events (``kind`` = the step name).

    Each entity starts at the first step on arrival. After a step it continues to the next with
    probability ``1 - dropout`` (a number, or one dropout per step but the last) after ``gap``,
    otherwise it leaves the funnel."""
    steps = ["visit", "view", "add_to_cart", "checkout"] if steps is None else steps
    c = _Check("event_sequence")
    names = c.names("steps", steps)
    if "entity_end" in names:
        c.fail("steps", "must not contain 'entity_end' (the engine's own event)", show=False)
    drops: list[float] = []
    if isinstance(dropout, list):
        if names and len(dropout) != len(names) - 1:
            c.fail(
                "dropout",
                f"list needs {len(names) - 1} values (one per step but the last)",
                len(dropout),
            )
        drops = [c.probability("dropout", d) for d in dropout]
    else:
        d = c.probability("dropout", dropout)
        drops = [d] * max(len(names) - 1, 0)
    gap_us = c.duration("gap", gap, positive=False)
    c.raise_if_any()

    states: dict[str, Any] = {
        "start": {"type": "initial", "transition": {"direct": f"step_{names[0]}"}}
    }
    for i, name in enumerate(names):
        state: dict[str, Any] = {"type": "event", "event": name}
        if i + 1 < len(names):
            nxt = f"step_{names[i + 1]}"
            state["delay"] = _exact_delay(gap_us)
            state["transition"] = {
                "distributed": [{"p": 1.0 - drops[i], "to": nxt}, {"p": drops[i], "to": "done"}]
            }
        else:
            state["transition"] = {"direct": "done"}
        states[f"step_{name}"] = state
    states["done"] = {"type": "terminal"}
    params = {
        "steps": list(names),
        "dropout": list(drops) if isinstance(dropout, list) else dropout,
        "gap": gap,
    }
    return _module(
        "event_sequence",
        params,
        states,
        _SPREAD,
        "A funnel of named events with a continue probability per step.",
    )


# -- telemetry_series ---------------------------------------------------------------------------


def telemetry_series(
    interval: str = "1 day",
    unit: str = "celsius",
    level: float = 20.0,
    noise: float = 0.5,
    drift: float = 0.1,
    missing_rate: float = 0.02,
    stuck_rate: float = 0.01,
) -> Module:
    """A regular reading every ``interval`` per entity (``kind`` ``reading``, ``value``, ``unit``).

    The reading is ``level`` plus ``drift`` per year of elapsed time plus gaussian noise of
    standard deviation ``noise``. A share ``missing_rate`` of the readings is skipped (no event);
    a share ``stuck_rate`` of the readings repeats the previous reading's value, so consecutive
    stuck readings form a stuck run. The first reading is taken when the entity arrives."""
    c = _Check("telemetry_series")
    interval_us = c.duration("interval", interval)
    if not isinstance(unit, str) or not unit:
        c.fail("unit", "must be a non-empty string", unit)
    lv = c.number("level", level)
    nz = c.non_negative("noise", noise)
    dr = c.number("drift", drift)
    miss = c.probability("missing_rate", missing_rate)
    stuck = c.probability("stuck_rate", stuck_rate)
    c.raise_if_any()

    fields = {"level": lv, "noise": nz, "drift": dr, "stuck_rate": stuck, "unit": unit}
    cycle = {"distributed": [{"p": 1.0 - miss, "to": "reading"}, {"p": miss, "to": "skipped"}]}
    states = {
        "start": {"type": "initial", "transition": cycle},
        "reading": {
            "type": "telemetry_reading",
            **fields,
            "skip": False,
            "delay": _exact_delay(interval_us),
            "transition": cycle,
        },
        "skipped": {
            "type": "telemetry_reading",
            **fields,
            "skip": True,
            "delay": _exact_delay(interval_us),
            "transition": cycle,
        },
    }
    params = {
        "interval": interval,
        "unit": unit,
        "level": level,
        "noise": noise,
        "drift": drift,
        "missing_rate": missing_rate,
        "stuck_rate": stuck_rate,
    }
    return _module(
        "telemetry_series",
        params,
        states,
        _AT_START,
        "A device reading at a regular interval, with drift, noise, skipped and stuck readings.",
    )


class TelemetryReading:
    """State type ``telemetry_reading``: one slot of a series (a reading, or a skipped slot)."""

    def validate(self, state: dict[str, Any]) -> list[str]:
        out = [
            f"needs a numeric {k!r}"
            for k in ("level", "noise", "drift", "stuck_rate")
            if not _is_number(state.get(k))
        ]
        if not isinstance(state.get("unit"), str):
            out.append("needs a text 'unit'")
        if not isinstance(state.get("skip"), bool):
            out.append("needs a boolean 'skip'")
        return out

    def uses(self, state: dict[str, Any]) -> dict[str, str]:
        return {"reading_origin": "num", "reading_last": "num"}

    def kinds(self, state: dict[str, Any]) -> list[str]:
        return ["reading"]

    def apply(self, ctx: StateContext) -> Emission | None:
        s = ctx.state
        origin = ctx.attribute("reading_origin")
        origin = np.where(np.isnan(origin), ctx.time.astype(np.float64), origin)
        ctx.set_attribute("reading_origin", origin)
        if s["skip"]:
            return None
        years = (ctx.time - origin) / UNIT_US["years"]
        u1, u2 = ctx.uniform(0), ctx.uniform(1)
        z = np.sqrt(-2.0 * np.log1p(-u1)) * np.cos(2.0 * np.pi * u2)
        fresh = np.round(s["level"] + s["drift"] * years + s["noise"] * z, 4)
        last = ctx.attribute("reading_last")
        value = np.where((ctx.uniform(2) < s["stuck_rate"]) & ~np.isnan(last), last, fresh)
        ctx.set_attribute("reading_last", value)
        return Emission(kind="reading", value=value, unit=s["unit"])


# -- transaction_stream -------------------------------------------------------------------------


def transaction_stream(
    rate: float = 24.0,
    amount: dict[str, float] | None = None,
    refund_rate: float = 0.05,
    reversal_rate: float = 0.01,
) -> Module:
    """Transactions as a Poisson process per entity, with refunds and reversals.

    ``rate`` is transactions per entity per year (exponential gaps). ``amount`` is the lognormal
    ``{"mu", "sigma"}`` of the ``value`` (default ``{"mu": 3.5, "sigma": 0.8}``). Each transaction
    is followed, before the next one, by a ``refund`` with probability ``refund_rate`` or a
    ``reversal`` with probability ``reversal_rate`` (at most one); the follow-up has the original's
    transaction id in ``ref`` and its ``value``. A transaction's id is in its ``code``."""
    amount = {"mu": 3.5, "sigma": 0.8} if amount is None else amount
    c = _Check("transaction_stream")
    lam = c.non_negative("rate", rate, positive=True)
    mu = sigma = 0.0
    if not isinstance(amount, dict):
        c.fail("amount", "must be an object with 'mu' and 'sigma'", amount)
    else:
        for key in amount:
            if key not in ("mu", "sigma"):
                c.fail("amount", f"has unknown key {key!r}", show=False)
        if "mu" not in amount or "sigma" not in amount:
            c.fail("amount", "needs 'mu' and 'sigma'", show=False)
        else:
            mu = c.number("amount.mu", amount["mu"])
            sigma = c.non_negative("amount.sigma", amount["sigma"])
    ref = c.probability("refund_rate", refund_rate)
    rev = c.probability("reversal_rate", reversal_rate)
    if not c.problems and ref + rev >= 1:
        c.fail("refund_rate + reversal_rate", "must be below 1", ref + rev)
    c.raise_if_any()

    q = ref + rev
    ev = {"type": "transaction_event", "mu": mu, "sigma": sigma}
    states: dict[str, Any] = {
        "start": {"type": "initial", "transition": {"direct": "idle"}},
        "idle": {
            "type": "simple",
            "delay": _exponential_years(lam),
            "transition": {"direct": "transaction"},
        },
        "transaction": {
            **ev,
            "role": "transaction",
            "transition": {"direct": "pending" if q else "idle"},
        },
    }
    if q:
        # While a transaction awaits its follow-up, the next transaction (rate lam) races a
        # follow-up (rate nu = lam * q / (1 - q)); the transaction hazard stays lam throughout, so
        # transactions are a Poisson process, and a follow-up wins the race with probability q.
        states["pending"] = {
            "type": "simple",
            "delay": _exponential_years(lam / (1.0 - q)),
            "transition": {
                "distributed": [
                    {"p": 1.0 - q, "to": "transaction"},
                    {"p": ref, "to": "refund"},
                    {"p": rev, "to": "reversal"},
                ]
            },
        }
        states["refund"] = {**ev, "role": "refund", "transition": {"direct": "idle"}}
        states["reversal"] = {**ev, "role": "reversal", "transition": {"direct": "idle"}}
    params = {
        "rate": rate,
        "amount": {"mu": mu, "sigma": sigma},
        "refund_rate": refund_rate,
        "reversal_rate": reversal_rate,
    }
    return _module(
        "transaction_stream",
        params,
        states,
        _AT_START,
        "Transactions as a Poisson process per entity with refunds and reversals referencing them.",
    )


class TransactionEvent:
    """State type ``transaction_event``: a transaction, or a refund/reversal of the last one."""

    ROLES = ("transaction", "refund", "reversal")

    def validate(self, state: dict[str, Any]) -> list[str]:
        out = [] if state.get("role") in self.ROLES else [f"'role' must be one of {self.ROLES}"]
        out += [f"needs a numeric {k!r}" for k in ("mu", "sigma") if not _is_number(state.get(k))]
        return out

    def uses(self, state: dict[str, Any]) -> dict[str, str]:
        return {"txn_count": "num", "txn_amount": "num"}

    def kinds(self, state: dict[str, Any]) -> list[str]:
        return [str(state["role"])]

    def apply(self, ctx: StateContext) -> Emission | None:
        s = ctx.state
        count = np.nan_to_num(ctx.attribute("txn_count"))
        if s["role"] == "transaction":
            count = count + 1.0
            amount = np.round(
                dist.sample(
                    {"kind": "lognormal", "mu": s["mu"], "sigma": s["sigma"]},
                    ctx.uniform(0),
                    ctx.uniform(1),
                ),
                2,
            )
            ctx.set_attribute("txn_count", count)
            ctx.set_attribute("txn_amount", amount)
            return Emission(kind="transaction", code=_txn_id(ctx.entity_id, count), value=amount)
        return Emission(
            kind=s["role"], ref=_txn_id(ctx.entity_id, count), value=ctx.attribute("txn_amount")
        )


def _txn_id(entity: Any, count: Any) -> Any:
    return np.char.add(
        np.char.add(entity.astype(str), "-"), count.astype(np.int64).astype(str)
    ).astype(object)


# -- file_arrival -------------------------------------------------------------------------------

PERIODS = {"hourly": "1 hour", "daily": "1 day", "weekly": "1 week"}


def file_arrival(
    schedule: str = "daily",
    late_rate: float = 0.1,
    late_delay: str | None = None,
    missing_rate: float = 0.03,
    duplicate_rate: float = 0.02,
) -> Module:
    """One entity per feed; each schedule slot ends in one of four outcomes.

    A slot (``daily``, ``hourly`` or ``weekly``, the first at the entity's arrival) is a
    ``file_arrived`` at the slot time, a ``file_late`` ``late_delay`` after it (probability
    ``late_rate``; default a quarter of the period), a ``file_missing`` at the slot time
    (``missing_rate``), or a ``file_arrived`` followed by a ``file_duplicate`` a tenth of a period
    later (``duplicate_rate``). Every event's ``payload`` is ``{"slot": "<slot time>"}``."""
    c = _Check("file_arrival")
    period_us = 0
    if schedule not in PERIODS:
        c.fail("schedule", f"must be one of {', '.join(sorted(PERIODS))}", schedule)
    else:
        period_us = parse_duration(PERIODS[schedule])
    late = c.probability("late_rate", late_rate)
    miss = c.probability("missing_rate", missing_rate)
    dup = c.probability("duplicate_rate", duplicate_rate)
    if not c.problems and late + miss + dup > 1 + 1e-12:
        c.fail("late_rate + missing_rate + duplicate_rate", "must not exceed 1", late + miss + dup)
    if late_delay is None:
        delay_us, delay_text = period_us // 4, f"{_seconds(period_us // 4):g} seconds"
    else:
        delay_us, delay_text = c.duration("late_delay", late_delay), late_delay
    if period_us and delay_us and delay_us >= period_us:
        c.fail(
            "late_delay",
            f"must be shorter than the schedule period ({PERIODS[schedule]})",
            late_delay,
        )
    c.raise_if_any()

    dup_gap = period_us // 10
    ok = max(0.0, 1.0 - late - miss - dup)
    ev = {"type": "file_event"}
    states: dict[str, Any] = {
        "start": {"type": "initial", "transition": {"direct": "slot"}},
        "slot": {
            "type": "simple",
            "transition": {
                "distributed": [
                    {"p": miss, "to": "missing"},
                    {"p": late, "to": "late_wait"},
                    {"p": dup, "to": "arrived_twice"},
                    {"p": ok, "to": "arrived"},
                ]
            },
        },
        "arrived": {
            **ev,
            "role": "arrived",
            "offset_us": 0,
            "delay": _exact_delay(period_us),
            "transition": {"direct": "slot"},
        },
        "missing": {
            **ev,
            "role": "missing",
            "offset_us": 0,
            "delay": _exact_delay(period_us),
            "transition": {"direct": "slot"},
        },
        "late_wait": {
            "type": "simple",
            "delay": _exact_delay(delay_us),
            "transition": {"direct": "late"},
        },
        "late": {
            **ev,
            "role": "late",
            "offset_us": delay_us,
            "delay": _exact_delay(period_us - delay_us),
            "transition": {"direct": "slot"},
        },
        "arrived_twice": {
            **ev,
            "role": "arrived",
            "offset_us": 0,
            "delay": _exact_delay(dup_gap),
            "transition": {"direct": "duplicate"},
        },
        "duplicate": {
            **ev,
            "role": "duplicate",
            "offset_us": dup_gap,
            "delay": _exact_delay(period_us - dup_gap),
            "transition": {"direct": "slot"},
        },
    }
    params = {
        "schedule": schedule,
        "late_rate": late_rate,
        "late_delay": delay_text,
        "missing_rate": missing_rate,
        "duplicate_rate": duplicate_rate,
    }
    return _module(
        "file_arrival",
        params,
        states,
        _AT_START,
        "A feed with an expected arrival per schedule slot: on time, late, missing or duplicated.",
    )


class FileEvent:
    """State type ``file_event``: the event of one slot outcome, with the slot in the payload."""

    KINDS = {
        "arrived": "file_arrived",
        "late": "file_late",
        "missing": "file_missing",
        "duplicate": "file_duplicate",
    }

    def validate(self, state: dict[str, Any]) -> list[str]:
        out = (
            []
            if state.get("role") in self.KINDS
            else [f"'role' must be one of {sorted(self.KINDS)}"]
        )
        offset = state.get("offset_us")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            out.append("needs a non-negative integer 'offset_us'")
        return out

    def kinds(self, state: dict[str, Any]) -> list[str]:
        return [self.KINDS[state["role"]]]

    def apply(self, ctx: StateContext) -> Emission | None:
        s = ctx.state
        slot = (ctx.time - s["offset_us"]).astype("datetime64[us]").astype("datetime64[s]")
        payload = np.char.add(np.char.add('{"slot": "', slot.astype(str)), '"}').astype(object)
        return Emission(kind=self.KINDS[s["role"]], payload=payload)


# -- entity_lifecycle ---------------------------------------------------------------------------


def entity_lifecycle(
    states: list[str] | None = None,
    update_rate: float = 4.0,
    delete_rate: float = 0.1,
) -> Module:
    """Create, any number of updates and an optional delete per entity (change-data-capture style).

    Every entity emits ``created`` on arrival (``text`` = the first of ``states``, ``value`` =
    version 1). While alive it is updated at ``update_rate`` per year (``updated``: ``text`` = a
    different state of ``states`` when there is more than one, ``value`` = the new version) and
    deleted at ``delete_rate`` per year (``deleted``: ``text`` = its state, ``value`` = its last
    version); nothing follows a delete."""
    states = ["active", "suspended", "closed"] if states is None else states
    c = _Check("entity_lifecycle")
    names = c.names("states", states)
    up = c.non_negative("update_rate", update_rate)
    de = c.non_negative("delete_rate", delete_rate)
    c.raise_if_any()

    total = up + de
    ev = {"type": "lifecycle_event", "states": list(names)}
    doc: dict[str, Any] = {
        "start": {"type": "initial", "transition": {"direct": "created"}},
        "deleted": {**ev, "role": "deleted", "transition": {"direct": "end"}},
        "end": {"type": "terminal"},
    }
    if total > 0:
        move = {
            "distributed": [{"p": up / total, "to": "updated"}, {"p": de / total, "to": "deleted"}]
        }
        gap = _exponential_years(total)
        doc["created"] = {**ev, "role": "created", "delay": gap, "transition": move}
        doc["updated"] = {**ev, "role": "updated", "delay": gap, "transition": move}
    else:
        doc["created"] = {**ev, "role": "created", "transition": {"direct": "end"}}
        doc["updated"] = {**ev, "role": "updated", "transition": {"direct": "end"}}
    params = {"states": list(names), "update_rate": update_rate, "delete_rate": delete_rate}
    return _module(
        "entity_lifecycle",
        params,
        doc,
        _SPREAD,
        "Create, update and delete events per entity, as a change-data-capture style stream.",
    )


class LifecycleEvent:
    """State type ``lifecycle_event``: a created, updated or deleted event with a version."""

    ROLES = ("created", "updated", "deleted")

    def validate(self, state: dict[str, Any]) -> list[str]:
        out = [] if state.get("role") in self.ROLES else [f"'role' must be one of {self.ROLES}"]
        names = state.get("states")
        if not isinstance(names, list) or not names or not all(isinstance(n, str) for n in names):
            out.append("needs a non-empty list of text 'states'")
        return out

    def uses(self, state: dict[str, Any]) -> dict[str, str]:
        return {"lifecycle_version": "num", "lifecycle_state": "num"}

    def kinds(self, state: dict[str, Any]) -> list[str]:
        return [str(state["role"])]

    def apply(self, ctx: StateContext) -> Emission | None:
        s = ctx.state
        names = np.array(s["states"], dtype=object)
        k = len(names)
        version = np.nan_to_num(ctx.attribute("lifecycle_version"))
        index = np.nan_to_num(ctx.attribute("lifecycle_state")).astype(np.int64)
        if s["role"] == "created":
            version = np.ones(len(index))
            index = np.zeros(len(index), np.int64)
        elif s["role"] == "updated":
            version = version + 1.0
            if k > 1:
                step = 1 + np.minimum((ctx.uniform(0) * (k - 1)).astype(np.int64), k - 2)
                index = (index + step) % k
        ctx.set_attribute("lifecycle_version", version)
        ctx.set_attribute("lifecycle_state", index.astype(np.float64))
        return Emission(kind=s["role"], text=names[index], value=version)


# -- registry -----------------------------------------------------------------------------------

PRIMITIVES: dict[str, Callable[..., Module]] = {
    "event_sequence": event_sequence,
    "telemetry_series": telemetry_series,
    "transaction_stream": transaction_stream,
    "file_arrival": file_arrival,
    "entity_lifecycle": entity_lifecycle,
}


def build(primitive: str, params: dict[str, Any] | None = None) -> Module:
    """The module of ``primitive`` built from ``params`` (a JSON object of keyword parameters).

    Raises :class:`PrimitiveParamsError` for an unknown primitive or parameter, and
    :class:`~shape_behavior.model.ModuleError` for an invalid value."""
    builder = PRIMITIVES.get(primitive)
    if builder is None:
        raise unknown_primitive(primitive)
    params = {} if params is None else params
    if not isinstance(params, dict):
        raise PrimitiveParamsError("params must be an object", "params")
    known = list(inspect.signature(builder).parameters)
    for key in params:
        if key not in known:
            raise PrimitiveParamsError(
                f"unknown parameter {key!r} for {primitive}; use one of {', '.join(known)}", key
            )
    return builder(**params)


def parameter_names(primitive: str) -> list[str]:
    """The parameter names of a primitive."""
    return list(inspect.signature(PRIMITIVES[primitive]).parameters)


register_state_type("telemetry_reading", TelemetryReading())
register_state_type("transaction_event", TransactionEvent())
register_state_type("file_event", FileEvent())
register_state_type("lifecycle_event", LifecycleEvent())

__all__ = [
    "PARAMS_FORMAT",
    "PARAMS_VERSION",
    "PRIMITIVES",
    "PrimitiveParamsError",
    "build",
    "entity_lifecycle",
    "event_sequence",
    "file_arrival",
    "parameter_names",
    "telemetry_series",
    "transaction_stream",
]
