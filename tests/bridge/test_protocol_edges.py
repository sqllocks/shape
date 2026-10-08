"""#546: the request size limit is in UTF-8 bytes of the request, result paths are absolute,
NumPy values stay numbers, `Bridge.handle` never raises, and finished job threads are let go."""

from __future__ import annotations

import dataclasses
import io
import json
import time
from pathlib import Path

import numpy as np

from shape.bridge.core import Bridge
from shape.bridge.handlers.common import jsonable
from shape.bridge.protocol import MAX_REQUEST_BYTES
from shape.bridge.registry import COMMANDS
from shape.bridge.server import serve


def _padded_list(size: int) -> str:
    head = '{"api_version": "1.0", "id": 1, "command": "list"'
    return head + " " * (size - len(head) - 1) + "}"


def test_a_request_of_exactly_the_limit_is_served_on_a_line(tmp_path):
    request = _padded_list(MAX_REQUEST_BYTES)
    assert len(request.encode("utf-8")) == MAX_REQUEST_BYTES
    out = io.StringIO()
    serve(Bridge(tmp_path / "jobs"), io.StringIO(request + "\n"), out)
    assert json.loads(out.getvalue())["ok"]


def test_the_limit_counts_utf8_bytes_not_characters(tmp_path):
    wide = '{"command": "list", "id": "' + "é" * (MAX_REQUEST_BYTES // 2 + 10) + '"}'
    assert len(wide) < MAX_REQUEST_BYTES < len(wide.encode("utf-8"))
    response = Bridge(tmp_path / "jobs").handle(wide)
    assert response["error"]["code"] == "usage.request_too_large"


def test_result_paths_are_absolute_with_a_relative_jobs_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bridge = Bridge(Path("jobs"))
    request = {
        "api_version": "1.0",
        "command": "preview",
        "args": {"domain": "retail", "rows": 50, "tables": ["store"]},
        "options": {"max_inline_bytes": 1024},
    }
    data = bridge.handle(json.dumps(request))["result"]["tables"]["store"]["data"]
    assert data["spilled"] is True and Path(data["path"]).is_absolute()
    assert Path(data["path"]).is_file()


def test_numpy_values_stay_json_numbers():
    value = {
        "i": np.int64(5),
        "b": np.bool_(True),
        "f": np.float32(1.5),
        "nan": np.float64("nan"),
        "a": np.array([1, 2]),
        "d": np.datetime64("2020-01-02"),
    }
    assert jsonable(value) == {
        "i": 5,
        "b": True,
        "f": 1.5,
        "nan": None,
        "a": [1, 2],
        "d": "2020-01-02",
    }


class _Unprintable:
    def __str__(self) -> str:
        raise RuntimeError("cannot be shown")


def test_handle_never_raises_on_a_result_it_cannot_convert(tmp_path, monkeypatch):
    broken = dataclasses.replace(COMMANDS["list"], handler=lambda args, ctx: {"x": _Unprintable()})
    monkeypatch.setitem(COMMANDS, "list", broken)
    response = Bridge(tmp_path / "jobs").handle(
        '{"api_version": "1.0", "id": 7, "command": "list"}'
    )
    assert response["ok"] is False and response["id"] == 7
    assert response["error"]["code"] == "internal.error"
    assert "RuntimeError" in response["error"]["message"]


def test_finished_job_threads_are_let_go(tmp_path):
    bridge = Bridge(tmp_path / "jobs")
    request = {
        "api_version": "1.0",
        "command": "dry_run",
        "args": {"domain": "retail"},
        "options": {"async": False},
    }
    ids = []
    for _ in range(3):
        doc = {**request, "command": "generate", "args": {"domain": "retail", "scale": "small"}}
        doc["options"] = {"async": True}
        ids.append(bridge.handle(json.dumps(doc))["result"]["job_id"])
        deadline = time.time() + 60
        while bridge.jobs.get(ids[-1])["status"] == "running" and time.time() < deadline:
            time.sleep(0.02)
    time.sleep(0.1)
    assert len(bridge.jobs._live) <= 1
