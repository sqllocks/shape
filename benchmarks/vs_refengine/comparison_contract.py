"""Reviewed noncomparability registry, authorized by the Owner on 2026-10-20.

Only an authenticated 0.9.1 error can use this category. Successful recordings,
including capture-date-dependent statistics and substituted data values, retain
every existing comparison. Names identify workloads; values are never rewritten.
"""

from typing import Any

from recorded_store import RecordingError, load

NOT_COMPARABLE_EXIT = 3
NOT_COMPARABLE_WORKLOADS = {
    "profile:edge/x_pq_list.parquet": {
        "category": "TypeError",
        "reason": (
            "Shape 0.9.1 recorded TypeError for a list-valued Parquet input; "
            "no baseline values exist."
        ),
    },
    "profile:edge/x_pq_nested.parquet": {
        "category": "TypeError",
        "reason": (
            "Shape 0.9.1 recorded TypeError for a struct-valued Parquet input; "
            "no baseline values exist."
        ),
    },
}


def not_comparable_reason(workload: str, baseline_profile: dict[str, Any]) -> str | None:
    """Classify a profile after the caller authenticates it and checks input digests."""
    entry = NOT_COMPARABLE_WORKLOADS.get(workload)
    if entry is not None and baseline_profile == {"__error__": {"category": entry["category"]}}:
        return entry["reason"]
    return None


def recorded_not_comparable_reason(workload: str) -> str | None:
    """Reauthenticate the reviewed release error when judging a worker's report."""
    if workload not in NOT_COMPARABLE_WORKLOADS:
        return None
    record = load("profile", workload)
    profile = record.get("profile")
    if not isinstance(profile, dict):
        raise RecordingError(workload + ": malformed recorded profile")
    return not_comparable_reason(workload, profile)


def reported_not_comparable(workload: str, rec: dict[str, Any]) -> str | None:
    """Reject forged categories, unexpected reasons and any timing/pass attribution."""
    ver = rec.get("verifier") or {}
    if (
        rec.get("kind") != "profile"
        or rec.get("dataset") != workload.removeprefix("profile:")
        or ver.get("status") != "not_comparable"
        or ver.get("exit_code") != NOT_COMPARABLE_EXIT
        or ver.get("workload") != workload
        or "first_run" in ver
        or rec.get("median_s") is not None
        or rec.get("runs_s") != []
        or rec.get("ratio_vs_baseline") is not None
        or rec.get("baseline_median_s") is not None
        or rec.get("median_s_1t") is not None
        or rec.get("cli_end_to_end_s") is not None
        or rec.get("cli_ratio_vs_baseline") is not None
    ):
        return None
    reason = recorded_not_comparable_reason(workload)
    return reason if reason is not None and ver.get("reason") == reason else None
