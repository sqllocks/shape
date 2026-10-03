"""#239: ``pid_alive`` must not call ``os.kill(pid, 0)`` on Windows, where signal 0 is
``CTRL_C_EVENT`` and the call sends Ctrl+C to a console process group."""

from __future__ import annotations

import os
import sys

import pytest

from shape.bridge import jobs
from shape.bridge.jobs import pid_alive

STILL_ACTIVE = 259
ERROR_ACCESS_DENIED = 5
ERROR_INVALID_PARAMETER = 87


class FakeKernel32:
    """The three kernel32 calls the Windows probe makes."""

    def __init__(self, *, handle=1, exit_code=STILL_ACTIVE, error=0, get_ok=True):
        self.handle, self.exit_code, self.error, self.get_ok = handle, exit_code, error, get_ok
        self.opened: list[int] = []
        self.closed: list[int] = []

    def open_process(self, access: int, pid: int) -> int:
        self.opened.append(pid)
        return self.handle

    def exit_code_of(self, handle: int) -> int | None:
        return self.exit_code if self.get_ok else None

    def close(self, handle: int) -> None:
        self.closed.append(handle)

    def last_error(self) -> int:
        return self.error


@pytest.fixture
def windows(monkeypatch):
    def install(**kw):
        fake = FakeKernel32(**kw)
        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(jobs, "_win_api", lambda: fake, raising=False)

        def no_kill(pid, sig):
            raise AssertionError(f"os.kill({pid}, {sig}) sends CTRL_C_EVENT on Windows")

        monkeypatch.setattr(os, "kill", no_kill)
        return fake

    return install


def test_windows_probe_never_calls_os_kill_and_sees_a_running_process(windows):
    fake = windows(exit_code=STILL_ACTIVE)
    assert pid_alive(4242) is True
    assert fake.opened == [4242]
    assert fake.closed == [fake.handle]


def test_windows_exited_process_is_not_alive(windows):
    fake = windows(exit_code=0)
    assert pid_alive(4242) is False
    assert fake.closed == [fake.handle]


def test_windows_missing_process_is_not_alive(windows):
    windows(handle=0, error=ERROR_INVALID_PARAMETER)
    assert pid_alive(4242) is False


def test_windows_access_denied_means_it_exists(windows):
    windows(handle=0, error=ERROR_ACCESS_DENIED)
    assert pid_alive(4242) is True


def test_windows_unreadable_exit_code_closes_the_handle_and_reports_dead(windows):
    fake = windows(get_ok=False)
    assert pid_alive(4242) is False
    assert fake.closed == [fake.handle]


@pytest.mark.parametrize("pid", [0, -1])
def test_non_positive_pids_are_never_alive(windows, pid):
    fake = windows()
    assert pid_alive(pid) is False
    assert fake.opened == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX probe")
def test_posix_probe_is_unchanged():
    assert pid_alive(os.getpid()) is True
    assert pid_alive(2**22 + 12345) is False
