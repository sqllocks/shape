"""Replay the authenticated implicit parsing date for two exact time-only fixtures.

This supplies an input to the parser before profiling. It never rewrites profile
values, rotates statistics, changes system time or changes comparison rules.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from paths import HERE, PROFILE_DATA_DIR
from recorded_store import RecordingError, filename, load, manifest, sha256

CONTEXT = HERE / "capture_date_context.json"
PROVENANCE = {
    "source_commit": "e41a67eab08beed36804bf3a84db83fb7f2595f6",
    "record_store_commit": "909a941cd294cd25031a8dcaa221e6b49e949674",
    "run_id": 38332677511,
    "job_id": 115085741837,
    "step": "Record every release workload, equivalence before timing",
    "window_start": "2026-10-13T01:47:38Z",
    "window_end": "2026-10-13T02:17:06Z",
    "session_start": "2026-10-13T01:47:50Z",
    "machine_local_start": "2026-10-13 01:47:50",
    "timezone": "UTC",
    "runner": "ubuntu-latest",
    "timezone_override": False,
}
WORKLOADS = {
    "edge/x_time_only.csv": {
        "record_sha256": "5abff56309718c807f460d931e36f97884f2d532ff565c4e616fcfe8213f6fa8",
        "input_sha256": "6aebbd5b39f54f1ca12d8380076f8b5707f485de24ae88d0f39eb7a056860094",
    },
    "edge/x_time_ampm.csv": {
        "record_sha256": "b233ad680bd0292f625c80eebd4bf29759ee69bb197396eafe2306dce778a48a",
        "input_sha256": "c1c846e830ae9dfa224e443b5b4b1e1356287d334536b208ac61475d8557e313",
    },
}


def capture_day(ds: str, data: Path, context: dict[str, Any] | None = None) -> dt.date:
    """Validate all provenance and original input/record digests before requesting replay."""
    try:
        doc = context if context is not None else json.loads(CONTEXT.read_text())
        if ds not in WORKLOADS:
            raise RecordingError("unknown capture-date replay workload")
        if doc.get("provenance") != PROVENANCE or doc.get("workloads") != WORKLOADS:
            raise RecordingError("capture-date replay provenance/context mismatch")
        p = doc["provenance"]
        start, end, session = [
            dt.datetime.fromisoformat(p[key].replace("Z", "+00:00"))
            for key in ("window_start", "window_end", "session_start")
        ]
        local = dt.datetime.fromisoformat(p["machine_local_start"])
        if (
            not start <= session <= end
            or start.date() != end.date()
            or local != session.replace(tzinfo=None)
            or p["timezone"] != "UTC"
            or p["timezone_override"] is not False
        ):
            raise RecordingError("ambiguous capture-date replay clock context")
        catalog = manifest()
        spec = WORKLOADS[ds]
        if (
            catalog["files"][filename("profile", ds)]["sha256"] != spec["record_sha256"]
            or catalog["recorded_utc"] != p["session_start"]
            or catalog["machine"].get("started") != p["machine_local_start"]
        ):
            raise RecordingError("capture-date recording context mismatch")
        inputs = {"profile/" + ds: sha256(data / ds)}
        if inputs != {"profile/" + ds: spec["input_sha256"]}:
            raise RecordingError("capture-date input digest mismatch")
        record = load("profile", ds, inputs=inputs)
        if record.get("recorded_utc") != p["session_start"]:
            raise RecordingError("capture-date workload timestamp mismatch")
        return start.date()
    except (KeyError, TypeError, ValueError, OSError) as exc:
        if isinstance(exc, RecordingError):
            raise
        raise RecordingError(
            "capture-date replay: malformed/unavailable authenticated context"
        ) from None


@contextmanager
def profile_date_context(ds: str, data: Path = PROFILE_DATA_DIR) -> Iterator[None]:
    """Replay only the implicit parser date; preserve explicit dates and restore on every exit."""
    if ds not in WORKLOADS:
        yield
        return
    day = capture_day(ds, data)
    from shape.profile.reference import dtparse

    original = dtparse._midnight
    captured = dt.datetime.combine(day, dt.time())

    def midnight(today: dt.datetime | None) -> dt.datetime:
        return original(captured if today is None else today)

    with patch.object(dtparse, "_midnight", midnight):
        yield
