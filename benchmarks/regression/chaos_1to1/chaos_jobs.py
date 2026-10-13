"""The workloads of the chaos parity harness: one list of job dicts, run identically by both
tools. Standard library only."""

from __future__ import annotations

from typing import Any

INTENSITIES = {"calm": 0.25, "moderate": 1.0, "stormy": 2.5, "hurricane": 5.0}

SUB_KINDS: dict[str, list[str]] = {
    "schema": ["add_column", "reorder", "drop_column", "rename_column", "retype_column"],
    "value": [
        "inject_nulls",
        "out_of_range",
        "wrong_types",
        "encoding_issues",
        "future_dates",
        "negative_amounts",
    ],
    "file": [
        "truncate",
        "corrupt_encoding",
        "partial_write",
        "zero_byte",
        "garbage_header",
        "wrong_delimiter",
        "invalid_json_poison",
        "bom_injection",
    ],
    "referential": ["orphan_fks", "duplicate_pks"],
    "temporal": ["late_arrivals", "out_of_order", "timezone_mismatch", "dst_boundary"],
    "volume": ["spike", "empty", "single_row"],
}
CATEGORIES = list(SUB_KINDS)
DATE_COLUMNS = ["created_at", "updated_at"]
SCHED_DAYS = 100


def build_jobs(sub_seeds: int, top_seeds: int, sched_seeds: int) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for cat, kinds in SUB_KINDS.items():
        for inten_name, inten in INTENSITIES.items():
            for kind in kinds:
                jobs.append(
                    {
                        "id": f"sub/{cat}/{kind}/{inten_name}",
                        "level": "sub",
                        "category": cat,
                        "kind": kind,
                        "intensity": inten,
                        "day": 30,
                        "seeds": list(range(1, sub_seeds + 1)),
                    }
                )
            days = [5, 30] if cat == "schema" else [30]
            for day in days:
                jobs.append(
                    {
                        "id": f"top/{cat}/{inten_name}/day{day}",
                        "level": "top",
                        "category": cat,
                        "intensity": inten,
                        "day": day,
                        "seeds": list(range(1, top_seeds + 1)),
                    }
                )
    for esc in ("gradual", "random", "front-loaded"):
        for inten_name in INTENSITIES:
            jobs.append(
                {
                    "id": f"sched/{esc}/{inten_name}",
                    "level": "sched",
                    "escalation": esc,
                    "intensity_name": inten_name,
                    "override": {"day": 15, "category": "value"},
                    "seeds": list(range(1, sched_seeds + 1)),
                }
            )
    jobs.append(
        {
            "id": "sched/disabled",
            "level": "sched",
            "escalation": "gradual",
            "intensity_name": "hurricane",
            "enabled": False,
            "seeds": [1, 2, 3],
        }
    )
    return jobs
