"""Authenticated execution-date replay precedes computation and never edits outputs."""

import copy
import datetime as dt
import importlib.util
import json
import sys

import pytest

from .test_profile_verify_recorded import BENCH, verifier


def replay_module():
    sys.path.insert(0, str(BENCH))
    spec = importlib.util.spec_from_file_location("capture_date", BENCH / "capture_date.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def inputs(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "clock_fixtures", BENCH / "profile_1to1/datasets.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.OUT = tmp_path
    mod.edge_deviations()
    return tmp_path


@pytest.mark.parametrize("host_date", [dt.datetime(2026, 10, 18), dt.datetime(2027, 1, 1)])
@pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
def test_original_value_comparisons_pass_on_other_host_dates(
    monkeypatch, inputs, host_date, dataset
):
    replay = replay_module()
    from shape.profile.reference import dtparse

    original = dtparse._midnight

    def host_midnight(today):
        return original(today if today is not None else host_date)

    monkeypatch.setattr(dtparse, "_midnight", host_midnight)
    v = verifier()
    v.DATA = inputs
    assert v.main([dataset]) == 0
    assert dtparse._midnight is host_midnight
    assert dtparse._midnight(None).date() == host_date.date()
    explicit = dt.datetime(2030, 5, 6)
    with replay.profile_date_context(dataset, inputs):
        assert dtparse._midnight(None).date() == dt.date(2026, 10, 13)
        assert dtparse._midnight(explicit).date() == explicit.date()


def test_context_restores_after_exception_and_other_workload_is_untouched(inputs):
    replay = replay_module()
    from shape.profile.reference import dtparse

    original = dtparse._midnight
    with pytest.raises(RuntimeError, match="interrupted"):
        with replay.profile_date_context("edge/x_time_only.csv", inputs):
            raise RuntimeError("interrupted")
    assert dtparse._midnight is original
    with replay.profile_date_context("ordinary.csv", inputs):
        assert dtparse._midnight is original


@pytest.mark.parametrize("control", ["source", "window", "timezone", "record", "input", "unknown"])
def test_capture_context_fails_closed(monkeypatch, inputs, control):
    replay = replay_module()
    doc = json.loads(replay.CONTEXT.read_text())
    dataset = "edge/x_time_only.csv"
    if control == "source":
        doc["provenance"]["source_commit"] = "0" * 40
    if control == "window":
        doc["provenance"]["window_end"] = "2026-10-14T02:17:06Z"
    if control == "timezone":
        doc["provenance"]["timezone"] = "unknown"
    if control == "record":
        doc["workloads"][dataset]["record_sha256"] = "0" * 64
    if control == "input":
        (inputs / dataset).write_text("id,t\n1,23:59:00\n")
    if control == "unknown":
        dataset = "edge/other.csv"
    with pytest.raises(replay.RecordingError):
        replay.capture_day(dataset, inputs, doc)


def test_authenticated_clock_does_not_hide_distribution_changes(monkeypatch, inputs):
    replay = replay_module()
    v = verifier()
    v.DATA = inputs
    ds = "edge/x_time_only.csv"
    with replay.profile_date_context(ds, inputs):
        candidate = v.shape_profile(ds)
    candidate = copy.deepcopy(candidate)
    candidate["columns"]["t"]["hour_histogram"] = [1.0] + [0.0] * 23
    monkeypatch.setattr(v, "shape_profile", lambda name: candidate)
    assert v.main([ds]) == 1


@pytest.mark.parametrize("control", ["valid", "modified_input"])
def test_timed_worker_reauthenticates_and_uses_same_parser_date(monkeypatch, inputs, control):
    replay = replay_module()
    from shape.profile.reference import dtparse

    from .test_profile_timing_recorded import bench

    module = bench()
    module.DATA = inputs
    ds = "edge/x_time_only.csv"
    v = verifier()
    v.DATA = inputs
    monkeypatch.setitem(sys.modules, "verify", v)
    original = dtparse._midnight

    def host(today):
        return original(today if today is not None else dt.datetime(2026, 10, 18))

    monkeypatch.setattr(dtparse, "_midnight", host)
    call = module.call_profile

    def checked(run, error, dataset):
        assert dtparse._midnight(None).date() == dt.date(2026, 10, 13)
        return call(run, error, dataset)

    monkeypatch.setattr(module, "call_profile", checked)
    if control == "modified_input":
        (inputs / ds).write_text("id,t\n1,23:59:00\n")
        # The replay helper, independently of the first T-22 load, must reject it.
        monkeypatch.setattr(module, "recorded_error", lambda ds: None)
        with pytest.raises(replay.RecordingError, match="input digest"):
            module.worker("shape", ds)
    else:
        module.worker("shape", ds)
    assert dtparse._midnight is host
