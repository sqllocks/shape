"""The reference runs of the incremental features, in the reference venv (needs pandas)."""

from __future__ import annotations

import sys
import warnings
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

sys.path.append(str(Path(__file__).resolve().parents[1]))
import _refpkg  # noqa: E402

warnings.filterwarnings("ignore")


def load_tables(directory: Path) -> dict[str, pd.DataFrame]:
    return {p.stem: pd.read_parquet(p) for p in sorted(directory.glob("*.parquet"))}


def domain_and_schema() -> tuple[Any, Any]:
    _resolve_domain = _refpkg.mod("cli")._resolve_domain
    RefEngine = getattr(_refpkg.mod("engine.generator"), _refpkg.ENGINE_CLASS)

    domain = _resolve_domain("retail", "3nf")
    return domain, RefEngine().describe(domain=domain)


def run_continue(
    start: dict[str, pd.DataFrame], schema: Any, seed: int, scenario: dict[str, Any]
) -> Any:
    """``ContinueEngine.continue_from`` as the reference command calls it, plus state
    transitions."""
    ContinueConfig = _refpkg.mod("incremental").ContinueConfig
    ContinueEngine = _refpkg.mod("incremental").ContinueEngine

    cfg = ContinueConfig(
        insert_count=scenario["inserts"],
        update_fraction=scenario["update_fraction"],
        delete_fraction=scenario["delete_fraction"],
        state_transitions=scenario.get("transitions", {}),
        seed=seed,
    )
    return ContinueEngine().continue_from({k: v.copy() for k, v in start.items()}, schema, cfg)


def run_time_travel(
    start: dict[str, pd.DataFrame], domain: Any, seed: int, scenario: dict[str, Any]
) -> Any:
    """``TimeTravelEngine.generate`` with month 0 replaced by the shared starting dataset.

    The reference engine builds month 0 itself with its own generator; its generator is replaced,
    in this process only, by one that returns ``start``, so both tools evolve the same data."""
    generator = _refpkg.mod("engine.generator")
    TimeTravelConfig = _refpkg.mod("incremental").TimeTravelConfig
    TimeTravelEngine = _refpkg.mod("incremental").TimeTravelEngine

    original = generator.RefEngine.generate

    def fixed(self: Any, *args: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(tables={k: v.copy() for k, v in start.items()})

    cfg = TimeTravelConfig(
        months=scenario["months"],
        growth_rate=scenario["growth_rate"],
        churn_rate=scenario["churn_rate"],
        update_fraction=scenario["update_fraction"],
        seasonality=dict(scenario.get("seasonality", {})),
        seed=seed,
    )
    generator.RefEngine.generate = fixed  # type: ignore[method-assign]
    try:
        return TimeTravelEngine().generate(domain=domain, config=cfg, scale="small")
    finally:
        generator.RefEngine.generate = original  # type: ignore[method-assign]


def key_maps(
    start: dict[str, pd.DataFrame], schema: Any
) -> tuple[dict[str, list[str]], dict[str, dict[str, str]]]:
    """The reference's own reading of primary and foreign keys (an oracle independent of
    Shape's)."""
    ContinueEngine = _refpkg.mod("incremental").ContinueEngine

    return ContinueEngine._pk_map(start, schema), ContinueEngine._fk_map(start, schema)


def run_time_travel_stepwise(
    start: dict[str, pd.DataFrame],
    domain: Any,
    seed: int,
    scenario: dict[str, Any],
    repair: Any,
) -> list[dict[str, pd.DataFrame]]:
    """The reference's engine one month at a time, with ``repair(tables)`` applied after every
    month.

    One ``TimeTravelEngine`` serves every step, so its key high-water mark persists as it does in a
    single run. Each step starts from the previous (repaired) snapshot and from the calendar month
    after it, so the seasonality multiplier of month ``m`` is the one a single run uses. This is the
    reference plus the documented foreign-key repair, month by month as Shape applies it."""
    import datetime as dt

    generator = _refpkg.mod("engine.generator")
    TimeTravelConfig = _refpkg.mod("incremental").TimeTravelConfig
    TimeTravelEngine = _refpkg.mod("incremental").TimeTravelEngine

    original = generator.RefEngine.generate
    engine = TimeTravelEngine()
    state = {k: v.copy() for k, v in start.items()}
    snapshots = [state]
    try:
        for month in range(1, scenario["months"] + 1):
            current = state

            def fixed(
                self: Any, *args: Any, _current: dict[str, pd.DataFrame] = current, **kwargs: Any
            ) -> Any:
                return SimpleNamespace(tables={k: v.copy() for k, v in _current.items()})

            index = 2023 * 12 + (month - 1)
            first = dt.date(index // 12, index % 12 + 1, 1)
            cfg = TimeTravelConfig(
                months=1,
                start_date=first.isoformat(),
                growth_rate=scenario["growth_rate"],
                churn_rate=scenario["churn_rate"],
                update_fraction=scenario["update_fraction"],
                seasonality=dict(scenario.get("seasonality", {})),
                seed=seed * 100 + month,
            )
            generator.RefEngine.generate = fixed  # type: ignore[method-assign]
            state = repair(
                engine.generate(domain=domain, config=cfg, scale="small").snapshots[1].tables
            )
            snapshots.append(state)
    finally:
        generator.RefEngine.generate = original  # type: ignore[method-assign]
    return snapshots
