"""Shared definitions of the simulation parity harness (P6-04): the fixed seeds, the baseline-name
to Shape-name map (D-13) and the helpers every case module uses. Standard library only: imported
from both the baseline venv and the Shape venv.

Layout (one module per simulator, so lanes P6-04a and P6-04b do not touch each other's files):

* ``verify.py``              the runner: discovers ``case_*.py``, runs every case, exits 1 on a
                             failure;
* ``baseline_worker.py``     runs one job on the baseline (baseline venv);
* ``shape_worker.py``        runs one job on Shape (Shape venv);
* ``sim_compare.py``         readers and comparers (pandas, scipy);
* ``case_<simulator>.py``    the jobs, both sides' code, the comparison, the probes of the
                             allow-list and the negative controls of one simulator.

A case module defines ``NAME`` (the simulator), ``ALLOWED`` (its allow-list entries), ``jobs(ctx)``
is not needed: it defines ``run(ctx) -> list[Check]``, ``baseline_side(job)`` and
``shape_side(job)`` (both import their library lazily, inside the function) and
``negative_controls(ctx) -> list[Check]`` (each Check there passes when the tampered output is
*caught*).
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from paths import BENCH_OUT_DIR  # noqa: E402

DOMAIN = "retail"

# T-21: the reference seed, the baseline's own spread (exactly 43-46) and Shape's seed. The seed
# set is fixed: there is no option, and a verdict from another set counts for nothing.
REF_SEED = 42
BASELINE_SEEDS = (43, 44, 45, 46)
SHAPE_SEED = 1042

# D-13: the baseline's names and the Shape names they stand for. The harness maps a baseline name
# to Shape's before it compares; nothing user-facing names the baseline. Recorded in the report.
NAME_MAP: dict[str, str] = {
    "_spindle_table": "_shape_table",
    "_spindle_seq": "_shape_seq",
    "schema_version": "schemaversion",  # event envelope attribute
    "correlation_id": "correlationid",  # event envelope attribute (manifests keep correlation_id)
    "_replay": "replay",
    "_replay_time": "replaytime",
    "spindle": "shape",  # envelope source value, and the prefix of the envelope type
    "spindle_events.jsonl": "events.jsonl",  # default event file
}
FIELD_MAP: dict[str, str] = {k: v for k, v in NAME_MAP.items() if k.startswith("_spindle")}
# Fields only Shape's events carry: the shared event-time field and the CloudEvents extensions.
SHAPE_ONLY_EVENT_FIELDS = {"_shape_event_time", "shapetable", "shapeseq"}
# Values the two tools generate on their own (wall clock, random ids): checked for form, never
# compared for equality.
VOLATILE = {"created_utc", "correlation_id", "correlationid", "time", "replaytime", "_restated_at"}


@dataclass
class Check:
    """One verdict of a case: ``ok`` and what was seen."""

    case: str
    name: str
    ok: bool
    detail: str = ""

    def line(self) -> str:
        return f"[{'PASS' if self.ok else 'FAIL'}] {self.case}: {self.name}" + (
            f" ({self.detail})" if self.detail else ""
        )


@dataclass
class Checks:
    """The verdicts of one case, collected as they are made."""

    case: str
    items: list[Check] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.items.append(Check(self.case, name, bool(ok), detail))
        return bool(ok)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.items)


def write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True, default=str), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def work_dir(*parts: str) -> Path:
    return BENCH_OUT_DIR.joinpath("simulation", *parts)
