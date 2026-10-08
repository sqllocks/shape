"""W1-11: constants kept in step, and small facts the docs state."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pyarrow as pa

import shape
from shape.privacy.redact import CaptureConfig, redact_profile


def test_literals_that_avoid_heavy_imports_match_their_source() -> None:
    from shape.cli import capture as cli_capture
    from shape.drift import engine
    from shape.generation import learn
    from shape.privacy import cells, redact, safe_profile

    assert engine.OTHER_BUCKET == learn.OTHER_BUCKET == cells.OTHER_BUCKET
    assert cli_capture.MODES == redact.MODES
    assert cli_capture.DEFAULT_MODE == redact.DEFAULT_MODE
    assert cli_capture.K_DEFAULT == safe_profile.K_DEFAULT


def test_a_sensitive_column_keeps_its_mean_and_spread(profile: Any) -> None:
    out = redact_profile(profile, CaptureConfig()).tables["table"]["columns"]["amount"]
    full = profile.tables["table"]["columns"]["amount"]
    assert out["mean"] == full["mean"] and out["std"] == full["std"]


def test_a_date_column_keeps_its_extremes_unless_it_is_sensitive() -> None:
    base = dt.datetime(2024, 1, 1)
    stamps = [base + dt.timedelta(minutes=17 * (i % 300)) for i in range(400)]
    p = shape.profile(pa.table({"t": pa.array(stamps, pa.timestamp("us"))}))
    kept = redact_profile(p, CaptureConfig()).tables["table"]["columns"]["t"]
    assert kept["min_value"] is not None and kept["max_value"] is not None
    hidden = redact_profile(p, CaptureConfig(classifications={"t": "SECRET"}))
    assert hidden.tables["table"]["columns"]["t"]["min_value"] is None
