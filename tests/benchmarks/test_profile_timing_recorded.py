"""T-22's authenticated refusal rule also applies inside the timed worker."""

import sys
from types import SimpleNamespace

import pytest

from .test_bench_scripts import BENCH, _load


def bench():
    return _load("recorded_profile_timing", BENCH / "profile_1to1/bench.py")


def test_matching_recorded_refusal_is_timed_as_a_successful_verdict():
    mod = bench()

    def refused():
        raise TypeError("nested Arrow column")

    assert mod.call_profile(refused, "TypeError", "edge/x_pq_list.parquet") == (None, True)


@pytest.mark.parametrize("control", ["unexpected_success", "wrong_category", "unrecorded_error"])
def test_profile_timing_refusal_negative_controls(control):
    mod = bench()

    def wrong():
        raise ValueError("wrong failure category")

    def refused():
        raise TypeError("unrecorded refusal")

    if control == "unexpected_success":
        with pytest.raises(RuntimeError, match="expected recorded TypeError.*succeeded"):
            mod.call_profile(lambda: object(), "TypeError", "edge/x_pq_list.parquet")
    elif control == "wrong_category":
        with pytest.raises(ValueError, match="wrong failure category"):
            mod.call_profile(wrong, "TypeError", "edge/x_pq_list.parquet")
    else:
        with pytest.raises(TypeError, match="unrecorded refusal"):
            mod.call_profile(refused, None, "d1.csv")


def test_ordinary_profile_result_is_unchanged():
    mod = bench()
    profile = object()
    assert mod.call_profile(lambda: profile, None, "d1.csv") == (profile, False)


@pytest.mark.parametrize("payload", [{"columns": {}}, {"__error__": {"category": "TypeError"}}])
def test_timing_error_category_comes_from_authenticated_t22_loader(monkeypatch, payload):
    mod = bench()
    seen = []

    def authenticated(ds):
        seen.append(ds)
        return payload

    monkeypatch.setitem(sys.modules, "verify", SimpleNamespace(baseline_profile=authenticated))
    assert mod.recorded_error("d1.csv") == payload.get("__error__", {}).get("category")
    assert seen == ["d1.csv"]


def test_timing_cannot_start_with_missing_or_tampered_record(monkeypatch):
    mod = bench()

    def unavailable(ds):
        raise mod.RecordingError("profile:" + ds + ": digest mismatch")

    monkeypatch.setitem(sys.modules, "verify", SimpleNamespace(baseline_profile=unavailable))
    with pytest.raises(mod.RecordingError, match="profile:d1.csv: digest mismatch"):
        mod.recorded_error("d1.csv")
