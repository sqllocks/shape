"""Run a library scenario and compare what happened with its answer key.

A scenario is either a *data* scenario (generate the domain, plant defects, run the validation
gates of ``docs/SCENARIO_PACKS.md``) or a *drift* scenario (a ``DriftPlan`` over the domain; profile
the touched tables on two days, diff them and list what the diff reports). The answer key
(``shape-scenario-expect``) says which gates must fail and which changes the diff must report;
:func:`mismatches` lists where the observed outcome departs from it.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from shape.scenario.library import formats
from shape.scenario.library.formats import (
    LibraryError,
    UnknownScenarioError,
    parse_expect,
    parse_index,
    parse_scenario,
    read_json,
)

# the diff kinds that mean the schema changed; one that no answer-key entry covers is a mismatch
STRUCTURAL = ("column_added", "column_removed", "dtype_change")


@dataclass
class Outcome:
    """What a scenario run observed."""

    scenario: str
    domain: str
    scale: str
    seed: int
    gates: dict[str, bool] = field(default_factory=dict)
    gate_messages: dict[str, str] = field(default_factory=dict)
    defects: dict[str, int] = field(default_factory=dict)
    drift: list[dict[str, Any]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenario": self.scenario,
            "domain": self.domain,
            "scale": self.scale,
            "seed": self.seed,
            "gates": self.gates,
            "gate_messages": self.gate_messages,
            "defects": self.defects,
            "drift": self.drift,
            "files": self.files,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }

    def summary(self) -> str:
        lines = [f"{self.scenario} ({self.domain}, scale {self.scale}, seed {self.seed})"]
        for gate, passed in self.gates.items():
            detail = "" if passed else f" ({self.gate_messages.get(gate, 'failed')})"
            lines.append(f"  gate {gate}: {'PASS' if passed else 'FAIL'}{detail}")
        for kind, rows in sorted(self.defects.items()):
            lines.append(f"  defect {kind}: {rows} rows")
        for window in self.drift:
            a, b = window["between"]
            found = ", ".join(f"{c['column']} {c['kind']}" for c in window["changes"]) or "none"
            lines.append(f"  diff of day {a} and day {b}: {found}")
        return "\n".join(lines)


@dataclass(frozen=True)
class Mismatch:
    """One expectation that the outcome did not meet."""

    expected: str
    observed: str

    def __str__(self) -> str:
        return f"expected {self.expected}; observed {self.observed}"


@dataclass
class ScenarioResult:
    outcome: Outcome
    mismatches: list[Mismatch]

    @property
    def met(self) -> bool:
        return not self.mismatches


# ---- the library ------------------------------------------------------------------------------


def list_scenarios(root: Path | None = None) -> list[dict[str, str]]:
    """The library index: ``id``, ``domain`` and ``description`` of every scenario."""
    base = root or formats.ROOT
    return parse_index(read_json(base / "index.json", "the library index"), "the library index")


def _directory(name: str, root: Path | None) -> Path:
    base = root or formats.ROOT
    known = [e["id"] for e in list_scenarios(base)]
    if name not in known:
        raise UnknownScenarioError(
            f"unknown scenario {name!r}; the library has: {', '.join(sorted(known))}"
        )
    return base / "scenarios" / name


def load_scenario(name: str, root: Path | None = None) -> dict[str, Any]:
    path = _directory(name, root) / "scenario.json"
    doc = parse_scenario(read_json(path, f"scenario {name}"), f"scenario {name}")
    if doc["id"] != name:
        raise LibraryError(f"{path} is the scenario {doc['id']!r}, listed as {name!r}")
    return doc


def load_expect(name: str, root: Path | None = None) -> dict[str, Any]:
    path = _directory(name, root) / "expect.json"
    doc = parse_expect(read_json(path, f"answer key of {name}"), f"answer key of {name}")
    if doc["scenario"] != name:
        raise LibraryError(f"{path} belongs to scenario {doc['scenario']!r}, not {name!r}")
    return doc


# ---- running ------------------------------------------------------------------------------------


def _schema(domain: str) -> Any:
    from shape.generation.domains import load_domain

    return copy.deepcopy(load_domain(domain).schema)


def run_scenario(
    name: str,
    *,
    scale: str | None = None,
    seed: int | None = None,
    output: str | Path | None = None,
    root: Path | None = None,
) -> ScenarioResult:
    """Run the library scenario ``name`` and compare it with its answer key.

    ``scale`` and ``seed`` default to the scenario's own. With ``output`` the generated tables are
    written under ``output/<name>/`` as Parquet. Raises :class:`LibraryError` for a scenario that
    is malformed or not in the library.
    """
    started = time.perf_counter()
    spec = load_scenario(name, root)
    expect = load_expect(name, root)
    scale = scale or str(spec.get("scale", "small"))
    seed = int(spec.get("seed", 42)) if seed is None else int(seed)
    unknown = [g for g in expect["gates_fail"] if g not in spec["gates"]]
    if unknown:
        raise LibraryError(
            f"answer key of {name} lists gates the scenario does not check: {', '.join(unknown)}"
        )
    schema = _schema(spec["domain"])
    out_dir = None if output is None else Path(output) / name
    outcome = Outcome(name, spec["domain"], scale, seed)
    if spec.get("drift"):
        _run_drift(spec, schema, outcome, out_dir)
    else:
        _run_data(spec, schema, outcome, out_dir)
    outcome.elapsed_seconds = time.perf_counter() - started
    return ScenarioResult(outcome, mismatches(expect, outcome))


def _generate(schema: Any, scale: str, seed: int) -> Any:
    from shape.generation.engine import Engine
    from shape.scenario.library.scale import resolve_scale

    preset, rows = resolve_scale(schema, scale)
    return Engine(schema, scale=preset, seed=seed, row_counts=rows).generate()


def _write(result: Any, directory: Path) -> list[str]:
    from shape.generation.output import write_result

    return [str(p) for p in write_result(result, "parquet", directory)]


def _run_data(spec: dict[str, Any], schema: Any, outcome: Outcome, out_dir: Path | None) -> None:
    from shape.scenario.library.defects import apply_defects
    from shape.scenario.runner import _run_gate

    generated = _generate(schema, outcome.scale, outcome.seed)
    tables, outcome.defects = apply_defects(
        generated.tables, list(spec.get("defects", [])), schema, outcome.seed
    )
    generated = replace(
        generated, tables=tables, row_counts={n: t.num_rows for n, t in tables.items()}
    )
    for gate in spec["gates"]:
        passed, message = _run_gate(gate, generated)
        outcome.gates[gate] = passed
        if not passed:
            outcome.gate_messages[gate] = message
    if out_dir is not None:
        outcome.files = _write(generated, out_dir)


def _run_drift(spec: dict[str, Any], schema: Any, outcome: Outcome, out_dir: Path | None) -> None:
    import shape
    from shape.generation.drift_plan import DriftPlan
    from shape.scenario.library.scale import resolve_scale

    plan = DriftPlan.from_dict(spec["drift"]["plan"])
    touched = sorted({e.table for e in plan.events})
    preset, rows = resolve_scale(schema, outcome.scale)
    days: dict[int, tuple[Any, dict[str, Any]]] = {}

    def day(n: int) -> dict[str, Any]:
        if n not in days:
            result = plan.generate_day(schema, n, scale=preset, row_counts=rows, seed=outcome.seed)
            profiles = {t: shape.profile(result[t], name=t) for t in touched}
            days[n] = (result, profiles)
            if out_dir is not None:
                outcome.files += _write(result, out_dir / f"day_{n}")
        return days[n][1]

    for a, b in spec["drift"]["compare"]:
        before, after = day(a), day(b)
        changes = {
            (f"{t}.{c['column']}", str(c["kind"]))
            for t in touched
            for c in shape.diff(before[t], after[t]).changes
        }
        outcome.drift.append(
            {
                "between": [a, b],
                "changes": [{"column": c, "kind": k} for c, k in sorted(changes)],
            }
        )


# ---- the answer key -------------------------------------------------------------------


def mismatches(expect: dict[str, Any], outcome: Outcome) -> list[Mismatch]:
    """Where ``outcome`` departs from the answer key ``expect`` (empty: every expectation met)."""
    out: list[Mismatch] = []
    for gate, passed in outcome.gates.items():
        must_fail = gate in expect["gates_fail"]
        if must_fail and passed:
            out.append(Mismatch(f"gate {gate} fails", f"gate {gate} passed"))
        elif not must_fail and not passed:
            why = outcome.gate_messages.get(gate, "failed")
            out.append(Mismatch(f"gate {gate} passes", f"gate {gate} failed ({why})"))
    for kind, bound in expect["defects"].items():
        planted = outcome.defects.get(kind, 0)
        if planted < bound["min"]:
            out.append(
                Mismatch(f"at least {bound['min']} rows of {kind}", f"{planted} rows of {kind}")
            )
    windows = {tuple(w["between"]): w for w in outcome.drift}
    for window in expect["drift"]:
        key = tuple(window["between"])
        seen = windows.get(key)
        if seen is None:
            out.append(Mismatch(f"a diff of days {key[0]} and {key[1]}", "no such window ran"))
            continue
        observed = {(c["column"], c["kind"]) for c in seen["changes"]}
        covered: set[tuple[str, str]] = set()
        for change in window["changes"]:
            hits = {(change["column"], k) for k in change["kinds"]} & observed
            covered |= hits
            if not hits:
                found = ", ".join(sorted(f"{c} {k}" for c, k in observed)) or "no change"
                out.append(
                    Mismatch(
                        f"{change['column']} reported as {' or '.join(change['kinds'])} "
                        f"between days {key[0]} and {key[1]}",
                        f"the diff reported {found}",
                    )
                )
        for column, kind in sorted(observed - covered):
            if kind in STRUCTURAL:
                out.append(
                    Mismatch(
                        f"no {kind} on {column} between days {key[0]} and {key[1]}",
                        f"{column} {kind}",
                    )
                )
    return out
