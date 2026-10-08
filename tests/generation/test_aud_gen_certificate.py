"""AUD-gen: ``certify`` reads capture documents only (#168)."""

from __future__ import annotations

import pyarrow as pa  # type: ignore[import-untyped]
import pytest

import shape
from shape.capture import capture_rows
from shape.generation import certify


def test_a_profile_is_not_taken_for_a_capture_and_certifies_nothing():
    # 168: a profile's columns share only ``null_count`` and ``mean`` with a capture, and None vs
    # None passed, so unrelated data was certified with score 1.0.
    t = pa.table({"s": [f"v{i % 4}" for i in range(500)]})
    profile = shape.profile(t, name="t").to_dict()
    with pytest.raises(ValueError, match="not a capture"):
        certify(profile, [{"s": "completely-different"}] * 10)


def test_a_capture_still_certifies_its_own_data():
    rows = [{"s": f"v{i % 4}", "x": i} for i in range(500)]
    cert = certify(capture_rows(rows).to_dict(), rows, tolerance=0.01)
    assert cert.passed and cert.score > 0.99
