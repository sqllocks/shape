"""BUGS-cli-1 #528: saving a connection profile is a read-modify-write under a file lock, so
two writers (threads or processes) never lose each other's profile."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time

import pytest

from shape.demo.connections import ConnectionProfile, ConnectionRegistry
from shape.demo.errors import DemoError

WRITER = """
import sys
from shape.demo.connections import ConnectionProfile, ConnectionRegistry
reg = ConnectionRegistry()
who = sys.argv[1]
for i in range(int(sys.argv[2])):
    reg.save(ConnectionProfile(name=f"{who}-{i}", local_path="/tmp/x"))
"""


def test_threads_saving_at_the_same_moment_keep_every_profile(tmp_path, monkeypatch):
    path = tmp_path / "connections.json"
    original = ConnectionRegistry._load_all

    def slow_load(self):  # widen the window between the read and the write
        data = original(self)
        time.sleep(0.05)
        return data

    monkeypatch.setattr(ConnectionRegistry, "_load_all", slow_load)
    names = [f"p{i}" for i in range(8)]
    barrier = threading.Barrier(len(names))
    errors: list[BaseException] = []

    def save(name):
        try:
            barrier.wait()
            ConnectionRegistry(path).save(ConnectionProfile(name=name, local_path="/tmp/x"))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=save, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(json.loads(path.read_text())) == sorted(names)


def test_processes_saving_at_the_same_moment_keep_every_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAPE_HOME", str(tmp_path / "home"))
    procs = [
        subprocess.Popen([sys.executable, "-c", WRITER, f"w{k}", "12"], env=_env(tmp_path))
        for k in range(6)
    ]
    assert [p.wait(timeout=120) for p in procs] == [0] * 6
    names = ConnectionRegistry().list()
    assert sorted(names) == sorted(f"w{k}-{i}" for k in range(6) for i in range(12))


def _env(tmp_path):
    import os

    return {**os.environ, "SHAPE_HOME": str(tmp_path / "home")}


def test_delete_and_save_do_not_lose_each_other(tmp_path):
    reg = ConnectionRegistry(tmp_path / "c.json")
    for n in ("a", "b"):
        reg.save(ConnectionProfile(name=n, local_path="/tmp/x"))
    reg.delete("a")
    reg.save(ConnectionProfile(name="c", local_path="/tmp/x"))
    assert reg.list() == ["b", "c"]


def test_a_held_lock_times_out_with_a_clear_error_and_loses_nothing(tmp_path, monkeypatch):
    from shape.demo import filelock

    target = tmp_path / "connections.json"
    reg = ConnectionRegistry(target)
    reg.save(ConnectionProfile(name="keep", local_path="/tmp/x"))
    monkeypatch.setattr(filelock, "DEFAULT_TIMEOUT", 0.2)
    with filelock.locked(target):
        with pytest.raises(DemoError, match="locked by another"):
            reg.save(ConnectionProfile(name="new", local_path="/tmp/x"))
    reg.save(ConnectionProfile(name="new", local_path="/tmp/x"))  # released: works again
    assert reg.list() == ["keep", "new"]


def test_the_lock_is_released_after_a_refused_profile(tmp_path):
    reg = ConnectionRegistry(tmp_path / "connections.json")
    with pytest.raises(DemoError):
        reg.save(ConnectionProfile(name="bad", auth_method="nope"))
    reg.save(ConnectionProfile(name="good", local_path="/tmp/x"))
    assert reg.list() == ["good"]


def test_the_lock_is_released_when_the_body_raises(tmp_path):
    from shape.demo import filelock

    target = tmp_path / "x.json"
    with pytest.raises(RuntimeError), filelock.locked(target):
        raise RuntimeError("boom")
    with filelock.locked(target, timeout=0.2):
        pass
