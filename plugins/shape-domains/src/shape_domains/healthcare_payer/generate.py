"""Public entry: simulate a population and return every table of the domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from .byo import LicensedTables
from .calibration import Calibration
from .claims import ClaimsBuilder
from .drugs import NdcDirectory
from .population import DEFAULT_STATES
from .rx import RxBuilder
from .simulate import Simulation, simulate
from .tables import (
    accumulator_table,
    claim_tables,
    member_tables,
    provider_table,
    risk_table,
    rx_tables,
)

TABLE_NAMES = (
    "member",
    "eligibility",
    "plan",
    "provider",
    "medical_claim",
    "medical_claim_line",
    "claim_diagnosis",
    "claim_procedure",
    "prior_authorization",
    "pharmacy_claim",
    "rx_order",
    "drug_reference",
    "rx_adherence",
    "member_risk",
    "member_accumulator",
)


@dataclass(slots=True)
class HealthcarePayerData:
    tables: dict[str, pa.Table]
    simulation: Simulation
    claims: ClaimsBuilder
    rx: RxBuilder
    licensed: LicensedTables
    stats: dict[str, Any] = field(default_factory=dict)

    def write(self, directory: str | Path, fmt: str = "parquet") -> list[Path]:
        out = Path(directory)
        out.mkdir(parents=True, exist_ok=True)
        written = []
        for name, table in self.tables.items():
            path = out / f"{name}.{fmt}"
            if fmt == "parquet":
                pq.write_table(table, path)
            elif fmt == "csv":
                import pyarrow.csv as pacsv  # type: ignore[import-untyped]

                pacsv.write_csv(table, path)
            else:
                raise ValueError(f"unknown format {fmt!r} (parquet, csv)")
            written.append(path)
        return written


def generate(
    n_members: int = 2000,
    *,
    seed: int = 42,
    start: date = date(2022, 1, 1),
    end: date = date(2024, 12, 31),
    states: tuple[str, ...] = DEFAULT_STATES,
    lob_mix: dict[str, float] | None = None,
    calibration: Calibration | None = None,
    licensed: LicensedTables | None = None,
    ndc: NdcDirectory | None = None,
    engine: str = "native",
) -> HealthcarePayerData:
    """A population of ``n_members`` over ``start``..``end`` (each member covered for part of it).

    Deterministic for a given ``seed``; independent of the number of members already generated."""
    lic = licensed or LicensedTables()
    sim = simulate(
        n_members,
        seed=seed,
        start=start,
        end=end,
        states=states,
        lob_mix=lob_mix,
        calibration=calibration,
        engine=engine,
    )
    cb = ClaimsBuilder(sim.persons, sim.plans, sim.directory, sim.cal, seed, start, end, lic)
    cb.run()
    rb = RxBuilder(
        sim.persons,
        sim.plans,
        sim.directory,
        sim.cal,
        seed,
        start,
        end,
        ndc=ndc,
        ind=cb.ind,
        fam=cb.fam,
    )
    rb.run()
    tables: dict[str, pa.Table] = {}
    tables.update(member_tables(sim.members, sim.plans, sim.directory))
    tables["provider"] = provider_table(sim.directory, lic)
    tables.update(claim_tables(cb, lic))
    tables.update(rx_tables(rb))
    tables["member_risk"] = risk_table(sim.members, cb, start, end)
    tables["member_accumulator"] = accumulator_table(cb, rb, sim.members, sim.plans)
    tables = {name: tables[name] for name in TABLE_NAMES}
    stats = {
        **sim.stats,
        **cb.stats,
        **rb.stats,
        "claims": len(cb.drafts),
        "rx_fills": len(rb.fills),
    }
    return HealthcarePayerData(tables, sim, cb, rb, lic, stats)
