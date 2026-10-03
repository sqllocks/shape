"""Rule mutation testing (W3-01): plant known faults, report which rules catch them.

Each mutant is one corruption of :mod:`shape.chaos.groundtruth` on one table and column. The
mutant is profiled in memory and checked against the contract; with ``diff`` (or when the contract
has a ``drift`` section) it is also compared with the unmutated profile under the contract's drift
policy. A mutant is *killed* when at least one rule or drift change fires. A mutant whose
ground-truth log is empty changed nothing, so it is ``not_applicable`` and outside the score.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

REPORT_FORMAT = "shape-mutation-report"
REPORT_VERSION = 1
PLAN_FORMAT = "shape-mutation-plan"
PLAN_VERSION = 1
DEFAULT_RATE = 0.05


class MutationError(ShapeError, ValueError):
    """The data, the contract or the plan cannot be used for mutation testing."""


@dataclass(frozen=True)
class MutantSpec:
    kind: str
    table: str
    column: str | None
    rate: float


@dataclass
class MutationResult:
    """The outcome of :func:`mutation_test`; ``to_dict()`` is the JSON report."""

    seed: int
    rate: float
    diff: bool
    mutants: list[dict[str, Any]]
    rules: list[str]
    rules_killed: dict[str, list[str]] = field(default_factory=dict)
    baseline_failed: list[str] = field(default_factory=list)

    @staticmethod
    def _score(items: list[dict[str, Any]]) -> dict[str, Any]:
        applicable = [m for m in items if m["status"] != "not_applicable"]
        killed = sum(1 for m in applicable if m["killed"])
        return {
            "killed": killed,
            "applicable": len(applicable),
            "score": round(killed / len(applicable), 6) if applicable else None,
        }

    @property
    def score(self) -> float | None:
        value = self._score(self.mutants)["score"]
        return None if value is None else float(value)

    def to_dict(self) -> dict[str, Any]:
        kinds = sorted({m["kind"] for m in self.mutants})
        tables = sorted({m["table"] for m in self.mutants})
        return {
            "format": REPORT_FORMAT,
            "version": REPORT_VERSION,
            "seed": self.seed,
            "rate": self.rate,
            "diff": self.diff,
            "mutants": [dict(m) for m in self.mutants],
            "score": {
                "overall": self._score(self.mutants),
                "by_kind": {
                    k: self._score([m for m in self.mutants if m["kind"] == k]) for k in kinds
                },
                "by_table": {
                    t: self._score([m for m in self.mutants if m["table"] == t]) for t in tables
                },
            },
            "rules": {
                rid: {"killed": list(self.rules_killed.get(rid, []))}
                for rid in [*self.rules, *(r for r in self.rules_killed if r not in self.rules)]
            },
            "rules_killed_none": [
                r
                for r in self.rules
                if not self.rules_killed.get(r) and r not in self.baseline_failed
            ],
            "baseline_failed_rules": sorted(self.baseline_failed),
        }


def _read_json(path: str | Path, what: str) -> Any:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError as exc:
        raise MutationError(f"{what} {path} is not valid JSON: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise MutationError(f"{what} {path} is not a text file: {exc}") from exc


def _check_rate(rate: Any, where: str) -> float:
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not 0.0 <= rate <= 1.0:
        raise MutationError(f"{where}: the rate is a number from 0 to 1, got {rate!r}")
    return float(rate)


def load_plan(plan: Mapping[str, Any] | str | Path) -> list[dict[str, Any]]:
    """The corruptions of a mutation plan (a dict or the path of a JSON file), validated."""
    doc = _read_json(plan, "plan") if isinstance(plan, (str, Path)) else plan
    if not isinstance(doc, Mapping) or doc.get("format") != PLAN_FORMAT:
        raise MutationError(f"a mutation plan is a JSON object with format {PLAN_FORMAT!r}")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise MutationError("the mutation plan needs an integer 'version'")
    if version > PLAN_VERSION:
        raise MutationError(
            f"the mutation plan is version {version}; this Shape reads up to version {PLAN_VERSION}"
        )
    if version < 1:
        raise MutationError(f"the mutation plan version is 1 or more, got {version}")
    items = doc.get("corruptions")
    if not isinstance(items, list) or not items:
        raise MutationError("the mutation plan needs a non-empty 'corruptions' list")
    allowed = {"kind", "table", "column", "rate"}
    out = []
    for n, item in enumerate(items):
        if not isinstance(item, Mapping) or "kind" not in item:
            raise MutationError(f"corruptions[{n}] is an object with a 'kind'")
        extra = set(item) - allowed
        if extra:
            raise MutationError(f"corruptions[{n}] has unknown keys: {sorted(extra)}")
        out.append(dict(item))
    return out


def _tables(data: Any) -> dict[str, Any]:
    import pyarrow as pa  # type: ignore[import-untyped]

    if isinstance(data, Mapping):
        tables = {str(k): v for k, v in data.items()}
    elif isinstance(data, pa.Table):
        tables = {"table": data}
    elif isinstance(data, (str, Path)):
        from shape.quality import load_tables

        try:
            tables = load_tables(data)
        except FileNotFoundError as exc:
            raise MutationError(f"data not found: {data}") from exc
    else:
        tables = {"table": pa.Table.from_pandas(data)}
    if not tables:
        raise MutationError(f"no data files found in {data}")
    return {
        name: t if isinstance(t, pa.Table) else pa.Table.from_pandas(t)
        for name, t in tables.items()
    }


def _specs(
    tables: Mapping[str, Any], plan: list[dict[str, Any]] | None, rate: float
) -> list[MutantSpec]:
    from shape.chaos.groundtruth import CORRUPTIONS, Corruption, _columns, _Run

    run = _Run(tables, 0, 0, {}, {})
    specs: list[MutantSpec] = []

    def auto(kind: str, table: str, c_rate: float) -> list[MutantSpec]:
        if kind == "duplicates":
            return [MutantSpec(kind, table, None, c_rate)]
        t = tables[table]
        if kind in ("pii_fill", "type_change", "null_creep"):
            import pyarrow as pa

            key = run.key_column(table)
            names = []
            for f in t.schema:
                text = pa.types.is_string(f.type) or pa.types.is_large_string(f.type)
                if f.name == key or f.name in run.foreign_keys(table):
                    continue
                if (kind == "type_change") == (not text) or (kind == "null_creep"):
                    names.append(f.name)
            return [MutantSpec(kind, table, n, c_rate) for n in names]
        return [
            MutantSpec(kind, table, n, c_rate)
            for n in _columns(run, Corruption(kind, rate=c_rate, table=table), table)
        ]

    if plan is None:
        for kind in CORRUPTIONS:
            for table in tables:
                specs += auto(kind, table, rate)
        return specs
    for n, item in enumerate(plan):
        kind = item["kind"]
        if kind not in CORRUPTIONS:
            raise MutationError(
                f"corruptions[{n}]: unknown corruption {kind!r}; choose one of "
                f"{', '.join(CORRUPTIONS)}"
            )
        c_rate = _check_rate(item.get("rate", rate), f"corruptions[{n}]")
        only, column = item.get("table"), item.get("column")
        if only is not None and only not in tables:
            raise MutationError(
                f"corruptions[{n}]: table {only!r} is not in the data ({', '.join(tables)})"
            )
        if column is not None and not isinstance(column, str):
            raise MutationError(f"corruptions[{n}]: the column is a name")
        names = [only] if only is not None else list(tables)
        if column is not None:
            holders = [t for t in names if column in tables[t].column_names]
            if not holders:
                where = f"table {only!r}" if only else "any table"
                raise MutationError(f"corruptions[{n}]: {where} has no column {column!r}")
            specs += [
                MutantSpec(kind, t, None if kind == "duplicates" else column, c_rate)
                for t in holders
            ]
        else:
            for t in names:
                specs += auto(kind, t, c_rate)
    return list(dict.fromkeys(specs))


def _mutant_id(s: MutantSpec) -> str:
    return f"{s.kind}.{s.table}" + (f".{s.column}" if s.column else "")


def _cells_changed(records: list[dict[str, Any]]) -> int:
    return sum(int(r["rows"]) if r.get("scope") == "column" else 1 for r in records)


def _profile(tables: Mapping[str, Any], dataset: bool) -> Any:
    import shape

    if dataset:
        return shape.profile(dict(tables))
    ((name, table),) = tables.items()
    return shape.profile(table, name=name)


def mutation_test(
    data: Any,
    contract: Mapping[str, Any] | str | Path,
    plan: Mapping[str, Any] | str | Path | None = None,
    seed: int = 0,
    rate: float = DEFAULT_RATE,
    diff: bool = False,
) -> MutationResult:
    """Score ``contract`` by planting faults in ``data``.

    ``data`` is a file, a folder, an Arrow table or a dict of Arrow tables. ``plan`` is a mutation
    plan (a dict or the path of its JSON file); without one every applicable corruption of every
    table and column is a mutant, at ``rate``. ``diff`` also compares each mutant with the
    unmutated profile under the contract's drift policy (always done when the contract has a
    ``drift`` section). The same ``seed`` gives the same result.
    """
    import shape
    from shape.chaos.groundtruth import Corruption, corrupt_tables
    from shape.contracts.v1 import ContractError, _load_contract, _validate_contract
    from shape.rules.evaluate import FAIL, contract_rule_ids, evaluate

    rate = _check_rate(rate, "--rate")
    try:
        doc = _load_contract(dict(contract) if isinstance(contract, Mapping) else contract)
        _validate_contract(doc)
    except (ContractError, OSError) as exc:
        raise MutationError(str(exc)) from exc
    tables = _tables(data)
    specs = _specs(tables, load_plan(plan) if plan is not None else None, rate)
    dataset = "tables" in doc or len(tables) > 1
    policy = doc.get("drift")
    compare = diff or policy is not None
    baseline = _profile(tables, dataset)
    names = list(tables)
    try:
        rules = contract_rule_ids(doc, names)
        base = evaluate(dict(baseline.tables), dataset, doc)
    except ContractError as exc:
        raise MutationError(str(exc)) from exc

    # a rule the unmutated data already fails cannot show that a mutant was caught
    base_failed = [o.id for o in base if o.status == FAIL]
    mutants: list[dict[str, Any]] = []
    killers: dict[str, list[str]] = {}
    for spec in specs:
        mid = _mutant_id(spec)
        corruption = Corruption(spec.kind, rate=spec.rate, table=spec.table, column=spec.column)
        try:
            outcome = corrupt_tables(tables, [corruption], seed=seed)
        except ValueError as exc:
            raise MutationError(f"{mid}: {exc}") from exc
        cells = _cells_changed(outcome.records)
        row: dict[str, Any] = {
            "id": mid,
            "kind": spec.kind,
            "table": spec.table,
            "column": spec.column,
            "rate": spec.rate,
            "seed": seed,
            "cells_changed": cells,
        }
        if cells == 0:
            row.update(status="not_applicable", killed=False, killed_by=[])
            mutants.append(row)
            continue
        mutated = _profile(outcome.tables, dataset)
        by = [
            o.id
            for o in evaluate(dict(mutated.tables), dataset, doc)
            if o.status == FAIL and o.id not in base_failed
        ]
        if compare:
            result = shape.diff(baseline, mutated, policy=policy)
            by += sorted({f"drift:{c['kind']}" for c in result.changes})
        by = list(dict.fromkeys(by))
        row.update(status="killed" if by else "survived", killed=bool(by), killed_by=by)
        for rid in by:
            killers.setdefault(rid, []).append(mid)
        mutants.append(row)
    return MutationResult(seed, rate, compare, mutants, rules, killers, base_failed)
