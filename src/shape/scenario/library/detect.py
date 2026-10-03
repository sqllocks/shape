"""What Shape's own checks report about a scenario: drift kinds, contract rules and gates.

The failure mode catalog, the detective packs and the canaries all ask the same question of a
scenario: *which checks fire?* A check is named ``drift:KIND`` (a ``shape diff`` change kind),
``rule:RULE`` (a ``shape check`` contract rule) or ``gate:NAME`` (a validation gate of
``docs/SCENARIO_PACKS.md``); the names are the real ones, and :func:`known_checks` lists them so a
catalog entry that names a check Shape does not have is caught.

A *run* of a scenario produces a baseline and a current set of tables. The baseline is the clean
batch (a data scenario without its defects, or the first day of a drift plan); the current one is
the batch with the problem. :func:`observe` profiles both, diffs them and checks the current
profile against a contract learned from the baseline (:func:`baseline_contract`), which is how a
team that wrote its contract from a good batch would catch the bad one.

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from shape.scenario.library.formats import LibraryError

if TYPE_CHECKING:
    import pyarrow as pa  # type: ignore[import-untyped]

KINDS = ("drift", "rule", "gate")
ENUM_LIMIT = 50  # a category set larger than this is not written into the contract
#: contract violations that name a rule outside ``_COLUMN_RULES`` (table rules and joint rules)
_EXTRA_RULES = (
    "row_count.min",
    "row_count.max",
    "required_column",
    "column_exists",
    "extra_column",
    "table_exists",
    "fd",
    "implies",
    "reference_pair",
    "max_implausible_rate",
)


@dataclass(frozen=True, order=True)
class Detection:
    """One check that fired: its name (``drift:null_rate_change``), and where."""

    check: str
    table: str
    column: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"check": self.check, "table": self.table, "column": self.column}


def known_checks() -> dict[str, frozenset[str]]:
    """The names of the checks Shape has, by kind: ``drift``, ``rule`` and ``gate``."""
    from shape.contracts.v1 import _COLUMN_RULES
    from shape.drift.engine import KIND_SEVERITY
    from shape.scenario.validator import KNOWN_GATES

    return {
        "drift": frozenset(KIND_SEVERITY),
        "rule": frozenset(_COLUMN_RULES) | frozenset(_EXTRA_RULES),
        "gate": frozenset(KNOWN_GATES),
    }


def parse_check(check: Any, what: str = "a check") -> tuple[str, str]:
    """``("drift", "null_rate_change")`` for ``"drift:null_rate_change"``. Raises
    :class:`LibraryError` for text of another shape or a kind that is not :data:`KINDS`."""
    if not isinstance(check, str) or ":" not in check:
        raise LibraryError(f"{what} must be written KIND:NAME with a kind of {', '.join(KINDS)}")
    kind, _, name = check.partition(":")
    if kind not in KINDS or not name:
        raise LibraryError(
            f"{what} {check!r} must be written KIND:NAME with a kind of {', '.join(KINDS)}"
        )
    return kind, name


def check_exists(check: str, what: str = "a check") -> None:
    """Raise :class:`LibraryError` when ``check`` names a drift kind, contract rule or gate that
    Shape does not have."""
    kind, name = parse_check(check, what)
    known = known_checks()[kind]
    if name not in known:
        raise LibraryError(
            f"{what} {check!r} names a {kind} that Shape does not have; "
            f"the {kind}s are: {', '.join(sorted(known))}"
        )


# ---- the contract of a clean batch ------------------------------------------------------------


def baseline_contract(table: Mapping[str, Any]) -> dict[str, Any]:
    """A ``shape-contract`` document that the profile ``table`` (one table, as
    ``Profile.to_dict()`` gives it) satisfies: its types, its nulls, its keys, its numeric bounds,
    its small category sets and its row count, and no placeholder values in the text columns that
    hold none."""
    from shape.contracts.v1 import _plain

    rows = int(table["row_count"])
    columns: dict[str, dict[str, Any]] = {}
    for name, col in table["columns"].items():
        rules: dict[str, Any] = {"dtype": col["dtype"]}
        if col.get("null_count") == 0:
            rules["nullable"] = False
        elif col.get("null_rate") is not None:
            rules["max_null_rate"] = min(1.0, round(float(col["null_rate"]) * 1.5 + 0.01, 4))
        if col.get("is_unique") is True:
            rules["unique"] = True
        if col["dtype"] in ("integer", "float", "datetime"):
            for bound, key in (("min", "min_value"), ("max", "max_value")):
                if col.get(key) is not None:
                    rules[bound] = _plain(col[key])
        if col["dtype"] == "string" and not col.get("placeholders"):
            rules["no_placeholder"] = True
        values = col.get("enum_values")
        if col.get("is_enum") and values and len(values) <= ENUM_LIMIT and col["dtype"] == "string":
            rules["allowed_values"] = sorted(values, key=str)
        columns[name] = rules
    return {
        "format": "shape-contract",
        "version": 1,
        "row_count": {"min": max(1, math.floor(rows * 0.5)), "max": max(2, math.ceil(rows * 2))},
        "required_columns": sorted(table["columns"]),
        "columns": columns,
    }


# ---- observing -----------------------------------------------------------------------------------


def observe(
    baseline: Mapping[str, pa.Table],
    current: Mapping[str, pa.Table],
    tables: list[str] | None = None,
) -> set[Detection]:
    """The drift kinds and contract rules that fire when ``current`` is compared with
    ``baseline``, for each of ``tables`` (default: every table the two share)."""
    import shape
    from shape.contracts.v1 import check

    names = tables if tables is not None else sorted(set(baseline) & set(current))
    found: set[Detection] = set()
    for name in names:
        before = shape.profile(baseline[name], name=name)
        after = shape.profile(current[name], name=name)
        for change in shape.diff(before, after).changes:
            found.add(Detection(f"drift:{change['kind']}", name, _bare(change["column"])))
        contract = baseline_contract(before.to_dict())
        for violation in check(after, copy.deepcopy(contract)).violations:
            found.add(Detection(f"rule:{violation['rule']}", name, violation["column"]))
    return found


def _bare(column: Any) -> str | None:
    """The column part of a change's ``column`` (``None`` for a change of the whole table)."""
    if column in (None, "", "*"):
        return None
    return str(column).split(".")[-1]


def gate_detections(gates: Mapping[str, bool], tables: list[str]) -> set[Detection]:
    """The gates of a data scenario that failed, as detections (a gate belongs to the batch, so
    they carry the scenario's first table)."""
    first = tables[0] if tables else ""
    return {Detection(f"gate:{g}", first) for g, passed in gates.items() if not passed}


# ---- a whole scenario ----------------------------------------------------------------------------


def scenario_detections(
    name: str,
    *,
    scale: str | None = None,
    seed: int | None = None,
    root: Path | None = None,
) -> set[Detection]:
    """Every check that fires for the library scenario ``name``.

    A data scenario is generated twice, clean and with its defects, and the gates run on the
    defective batch; a drift scenario compares the days of its ``compare`` windows. Only the
    tables the scenario touches are profiled."""
    from dataclasses import replace

    from shape.scenario.library import run
    from shape.scenario.library.defects import apply_defects
    from shape.scenario.runner import _run_gate

    spec = run.load_scenario(name, root)
    scale = scale or str(spec.get("scale", "small"))
    seed = int(spec.get("seed", 42)) if seed is None else int(seed)
    schema = run._schema(spec["domain"])
    found: set[Detection] = set()
    if spec.get("drift"):
        from shape.generation.drift_plan import DriftPlan
        from shape.scenario.library.scale import resolve_scale

        plan = DriftPlan.from_dict(spec["drift"]["plan"])
        touched = sorted({e.table for e in plan.events})
        preset, rows = resolve_scale(schema, scale)
        days: dict[int, Any] = {}

        def day(n: int) -> Any:
            if n not in days:
                days[n] = plan.generate_day(schema, n, scale=preset, row_counts=rows, seed=seed)
            return days[n]

        for a, b in spec["drift"]["compare"]:
            found |= observe(day(a).tables, day(b).tables, touched)
        return found
    clean = run._generate(schema, scale, seed)
    tables, _ = apply_defects(clean.tables, list(spec.get("defects", [])), schema, seed)
    touched = sorted({str(d["table"]) for d in spec.get("defects", [])})
    current = replace(clean, tables=tables, row_counts={n: t.num_rows for n, t in tables.items()})
    gates = {g: _run_gate(g, current)[0] for g in spec["gates"]}
    found |= observe(clean.tables, tables, touched)
    found |= gate_detections(gates, touched)
    return found
