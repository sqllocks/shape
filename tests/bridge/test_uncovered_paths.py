"""Paths of the bridge no other test reached: the scale-job error codes, `writing`, `pid_alive`,
the JSON conversion of dates, decimals and bytes, a profile on a schema file, and a spill that
fails half-way."""

from __future__ import annotations

import datetime as dt
import decimal
import os
import sys

import pytest

from shape.bridge.context import Context
from shape.bridge.errors import to_bridge_error, writing
from shape.bridge.handlers.common import jsonable
from shape.bridge.jobs import pid_alive
from shape.bridge.protocol import BridgeError


def test_scale_job_errors_have_their_codes():
    from shape.scale.jobs import JobNotFoundError, JobStateError

    assert to_bridge_error(JobNotFoundError("job-x")).code == "input.unknown_job"
    assert to_bridge_error(JobStateError("running")).code == "input.job_state"


def test_writing_turns_an_os_error_into_a_write_failure_but_not_a_bare_not_found():
    with pytest.raises(BridgeError) as caught, writing():
        raise PermissionError(13, "denied", "/x")
    assert caught.value.code == "io.write_failed"
    with pytest.raises(FileNotFoundError), writing():
        raise FileNotFoundError("no file name")


def test_pid_alive(monkeypatch):
    assert pid_alive(0) is False and pid_alive(os.getpid()) is True

    def kill(error):
        def raise_(pid, sig):
            raise error

        return raise_

    # The POSIX probe's error mapping, on every platform (Windows asks the process table,
    # tests/bridge/test_pid_alive_windows.py).
    monkeypatch.setattr(sys, "platform", "linux")
    for error, alive in (
        (ProcessLookupError(), False),
        (PermissionError(), True),
        (OSError("other"), False),
    ):
        monkeypatch.setattr(os, "kill", kill(error))
        assert pid_alive(4242) is alive


def test_jsonable_dates_decimals_and_bytes():
    assert jsonable(
        {
            "t": dt.time(1, 2),
            "d": dt.timedelta(seconds=90),
            "m": decimal.Decimal("1.10"),
            "b": b"\x00\xff",
            "s": {3},
        }
    ) == {"t": "01:02:00", "d": 90.0, "m": "1.10", "b": "AP8=", "s": [3]}


def test_a_profile_on_a_schema_file_is_refused(api, schema_file):
    e = api.fail("describe", "input.invalid_value", domain=str(schema_file), profile="peak")
    assert "schema file" in e["message"]


def test_a_spill_that_fails_leaves_no_partial_file(bridge, monkeypatch):
    ctx = Context(bridge.jobs, options={"max_inline_bytes": 1024})

    def fail(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(BridgeError) as caught:
        ctx.spill("big", ["x" * 2000])
    assert caught.value.code == "io.write_failed"
    assert list(ctx.results_dir.iterdir()) == []
