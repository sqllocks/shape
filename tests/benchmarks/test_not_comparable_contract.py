"""Owner-approved refusals are accounted separately, never verified or timed."""

import copy
import datetime as dt
import importlib.util
import sys

import pytest

from .test_nightly_suite import BENCH, _write_night, nightly, run
from .test_profile_verify_recorded import profile, verifier

WORKLOAD = "profile:edge/x_pq_list.parquet"


def contract():
    spec = importlib.util.spec_from_file_location(
        "comparison_contract", BENCH / "comparison_contract.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_registry_contains_only_authenticated_release_errors():
    c = contract()
    from recorded_store import load

    assert set(c.NOT_COMPARABLE_WORKLOADS) == {WORKLOAD, "profile:edge/x_pq_nested.parquet"}
    for wid, entry in c.NOT_COMPARABLE_WORKLOADS.items():
        record = load("profile", wid)
        assert c.not_comparable_reason(wid, record["profile"]) == entry["reason"]
        assert entry["reason"]
        for value in ({"columns": {}}, {"__error__": {"category": "ValueError"}}):
            assert c.not_comparable_reason(wid, value) is None
    assert (
        c.not_comparable_reason("profile:other", {"__error__": {"category": "TypeError"}}) is None
    )


def test_registered_refusal_never_profiles_or_prints_pass(monkeypatch, capsys):
    c, v = contract(), verifier()
    monkeypatch.setattr(v, "baseline_profile", lambda ds: {"__error__": {"category": "TypeError"}})
    monkeypatch.setattr(
        v, "shape_profile", lambda ds: pytest.fail("refusal must not be timed/profiled")
    )
    assert v.main([WORKLOAD.removeprefix("profile:")]) == c.NOT_COMPARABLE_EXIT
    output = capsys.readouterr().out
    assert "NOT COMPARABLE " + WORKLOAD in output and "PASS" not in output


def test_registered_name_cannot_bypass_missing_or_tampered_record(monkeypatch, capsys):
    v = verifier()
    from recorded_store import RecordingError

    def unavailable(ds):
        raise RecordingError("profile:" + ds + ": digest mismatch")

    monkeypatch.setattr(v, "baseline_profile", unavailable)
    assert v.main([WORKLOAD.removeprefix("profile:")]) == 2
    output = capsys.readouterr()
    assert "digest mismatch" in output.err and "NOT COMPARABLE" not in output.out


@pytest.mark.parametrize("field", ["dow_histogram", "enum_values", "mean"])
def test_value_fields_remain_comparable_for_registered_name(monkeypatch, field):
    contract()
    v = verifier()
    baseline = profile(v)
    baseline["columns"]["x"]["dow_histogram"] = [0, 1, 0, 0, 0, 0, 0]
    candidate = copy.deepcopy(baseline)
    col = candidate["columns"]["x"]
    if field == "dow_histogram":
        col[field] = [0, 0, 0, 0, 0, 0, 1]
    elif field == "enum_values":
        col[field] = {"synthetic": 1.0}
    else:
        col[field] += 1
    monkeypatch.setattr(v, "baseline_profile", lambda ds: baseline)
    monkeypatch.setattr(v, "shape_profile", lambda ds: candidate)
    assert v.main([WORKLOAD.removeprefix("profile:")]) == 1


def test_profile_runner_accounts_refusal_without_timing(monkeypatch):
    c = contract()
    calls = []

    def shell(cmd):
        calls.append(cmd)
        return (c.NOT_COMPARABLE_EXIT if "verify.py" in cmd[1] else 0), ""

    monkeypatch.setattr(run, "sh", shell)
    out = {"baseline": {"workloads": {}}, "shape": {"workloads": {}}}
    assert run.profile_workloads([WORKLOAD.removeprefix("profile:")], 5, out)
    rec = out["shape"]["workloads"][WORKLOAD]
    assert rec["verifier"]["status"] == "not_comparable"
    assert rec["verifier"]["reason"] == c.NOT_COMPARABLE_WORKLOADS[WORKLOAD]["reason"]
    assert rec["median_s"] is None and not rec["runs_s"]
    assert not any("bench.py" in str(cmd) for cmd in calls)


def test_time_only_weekday_is_a_value_even_when_current_weekday_matches_capture():
    from shape.profile.reference.dtparse import parse_mixed

    # The frozen recording's Tuesday happens to match 2026-10-20. It must not
    # conceal the same input's different value on another day.
    tuesday = parse_mixed("09:00:00", today=dt.datetime(2026, 10, 13))
    sunday = parse_mixed("09:00:00", today=dt.datetime(2026, 10, 18))
    assert tuesday.time() == sunday.time()
    assert (tuesday.weekday(), sunday.weekday()) == (1, 6)


@pytest.mark.parametrize(
    "control",
    [
        "valid",
        "forged_name",
        "wrong_reason",
        "timed",
        "wrong_exit",
        "runs",
        "ratio",
        "first_run",
        "kind",
        "dataset",
        "tampered_record",
        "single_thread_timing",
        "registered_error_as_pass",
    ],
)
def test_nightly_reports_refusal_separately_and_rejects_forgery(tmp_path, monkeypatch, control):
    c = contract()
    if control == "tampered_record":
        from recorded_store import RecordingError

        def tampered(*args):
            raise RecordingError("digest mismatch")

        monkeypatch.setattr(c, "load", tampered)
        # Nightly holds the module imported before this test's independent loader.
        monkeypatch.setitem(nightly.reported_not_comparable.__globals__, "load", tampered)

    def mutate(name, result):
        if name != "profile":
            return
        for registered, entry in c.NOT_COMPARABLE_WORKLOADS.items():
            refused = result["shape"]["workloads"][registered]
            refused.update(median_s=None, runs_s=[], ratio_vs_baseline=None)
            refused.pop("baseline_median_s", None)
            refused["verifier"] = {
                "status": "not_comparable",
                "exit_code": 3,
                "command": "verify.py",
                "workload": registered,
                "reason": entry["reason"],
            }
        wid = "profile:d1.csv" if control == "forged_name" else WORKLOAD
        rec = result["shape"]["workloads"][wid]
        rec.update(median_s=None, runs_s=[], ratio_vs_baseline=None)
        rec.pop("baseline_median_s", None)
        rec["verifier"] = {
            "status": "not_comparable",
            "exit_code": 3,
            "command": "verify.py",
            "workload": wid,
            "reason": c.NOT_COMPARABLE_WORKLOADS[WORKLOAD]["reason"],
        }
        if control == "wrong_reason":
            rec["verifier"]["reason"] = "unreviewed reason"
        if control == "timed":
            rec["median_s"] = 1
        if control == "wrong_exit":
            rec["verifier"]["exit_code"] = 0
        if control == "runs":
            rec["runs_s"] = [1.0]
        if control == "ratio":
            rec["ratio_vs_baseline"] = 1.0
        if control == "first_run":
            rec["verifier"]["first_run"] = {"status": "pass", "exit_code": 0}
        if control == "kind":
            rec["kind"] = "generate"
        if control == "dataset":
            rec["dataset"] = "d1.csv"
        if control == "single_thread_timing":
            rec["median_s_1t"] = 1.0
        if control == "registered_error_as_pass":
            rec["verifier"].update(status="pass", exit_code=0)
            rec.update(median_s=1.0, runs_s=[1.0], ratio_vs_baseline=1.0)

    _, verdict = nightly.merge(_write_night(tmp_path, mutate=mutate), run=run)
    assert verdict["green"] == (control == "valid")
    if control == "valid":
        assert verdict["passed"] == verdict["required"] - 2
        assert verdict["not_comparable"] == [
            {"implementation": "shape", "workload": wid, "reason": entry["reason"]}
            for wid, entry in sorted(c.NOT_COMPARABLE_WORKLOADS.items())
        ]
    else:
        assert verdict["failed"]
