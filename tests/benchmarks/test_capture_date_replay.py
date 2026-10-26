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


@pytest.mark.parametrize("location", ["context", "provenance", "workload"])
def test_unexpected_date_field_fails_closed(inputs, location):
    replay = replay_module()
    doc = json.loads(replay.CONTEXT.read_text())
    dataset = "edge/x_time_only.csv"
    target = {
        "context": doc,
        "provenance": doc["provenance"],
        "workload": doc["workloads"][dataset],
    }[location]
    target["unexpected_date"] = "2030-01-01"
    with pytest.raises(replay.RecordingError):
        replay.capture_day(dataset, inputs, doc)


@pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
@pytest.mark.parametrize(
    "text,expected",
    [
        ("2030-05-06", dt.datetime(2030, 5, 6)),
        ("2030-05-06 14:23:45", dt.datetime(2030, 5, 6, 14, 23, 45)),
        ("05/06/2030", dt.datetime(2030, 5, 6)),
        ("May 6, 2030 2:23 PM", dt.datetime(2030, 5, 6, 14, 23)),
        ("2024-02-29", dt.datetime(2024, 2, 29)),
    ],
)
def test_replay_preserves_data_derived_dates(inputs, dataset, text, expected):
    replay = replay_module()
    from shape.profile.reference import dtparse

    before = dtparse.coerce_column([text])
    assert before == [expected]
    with replay.profile_date_context(dataset, inputs):
        assert dtparse.parse_mixed(text) == expected
        assert dtparse.coerce_column([text]) == before


@pytest.mark.parametrize("dataset", ["edge/x_time_only.csv", "edge/x_time_ampm.csv"])
@pytest.mark.parametrize("text", ["14:23:45", "2:23:45 PM"])
def test_replay_changes_only_clock_derived_default_date(monkeypatch, inputs, dataset, text):
    replay = replay_module()
    from shape.profile.reference import dtparse

    original = dtparse._midnight
    host = dt.datetime(2032, 7, 19)
    monkeypatch.setattr(dtparse, "_midnight", lambda today: original(today or host))
    assert dtparse.coerce_column([text]) == [dt.datetime(2032, 7, 19, 14, 23, 45)]
    with replay.profile_date_context(dataset, inputs):
        expected = dt.datetime(2026, 10, 13, 14, 23, 45)
        assert dtparse.parse_mixed(text) == expected
        assert dtparse.coerce_column([text]) == [expected]
        assert dtparse.coerce_column([text], today=host) == [dt.datetime(2032, 7, 19, 14, 23, 45)]
    assert dtparse.coerce_column([text]) == [dt.datetime(2032, 7, 19, 14, 23, 45)]
