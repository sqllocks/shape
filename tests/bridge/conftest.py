"""Fixtures of the bridge tests."""

from __future__ import annotations

import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

import pytest

from shape.bridge.core import Bridge

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


@pytest.fixture(autouse=True)
def _jobs_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SHAPE_JOBS_DIR", str(tmp_path / "default-jobs"))
    monkeypatch.delenv("SHAPE_FABRIC_TOKEN", raising=False)
    monkeypatch.delenv("SHAPE_FABRIC_STORAGE_TOKEN", raising=False)


@pytest.fixture
def jobs_dir(tmp_path) -> Path:
    return tmp_path / "jobs"


@pytest.fixture
def bridge(jobs_dir) -> Bridge:
    return Bridge(jobs_dir)


class Caller:
    """``call("generate", domain=...)`` returns the response; ``ok(...)`` the result."""

    def __init__(self, bridge: Bridge) -> None:
        self.bridge = bridge

    def call(
        self, command: str, options: dict[str, Any] | None = None, **args: Any
    ) -> dict[str, Any]:
        request: dict[str, Any] = {"api_version": "1.0", "command": command, "args": args}
        if options:
            request["options"] = options
        response = self.bridge.handle(json.dumps(request))
        json.dumps(response, allow_nan=False)  # every response is plain JSON
        return response

    def ok(self, command: str, options: dict[str, Any] | None = None, **args: Any) -> Any:
        response = self.call(command, options, **args)
        assert response["ok"], response
        return response["result"]

    def fail(
        self, command: str, code: str, options: dict[str, Any] | None = None, **args: Any
    ) -> dict[str, Any]:
        response = self.call(command, options, **args)
        assert not response["ok"], response
        assert response["error"]["code"] == code, response["error"]
        return response["error"]


@pytest.fixture
def api(bridge) -> Caller:
    return Caller(bridge)


@pytest.fixture
def schema_file(tmp_path) -> Path:
    from scale_schemas import plain_doc

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc(ROWS)))
    return path


def write_csv(path: Path, shift: int = 0, rows: int = 500) -> Path:
    rng = random.Random(1)
    with open(path, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["id", "email", "status", "amount"])
        for i in range(rows):
            status = rng.choice(["new", "paid", "refund" if shift else "void"])
            out.writerow(
                [i, f"user{i}@example.com", status, round(rng.gauss(100 + shift * 50, 20))]
            )
    return path


@pytest.fixture
def csv_pair(tmp_path) -> tuple[Path, Path]:
    return write_csv(tmp_path / "a.csv"), write_csv(tmp_path / "b.csv", shift=1)


def write_wide(path: Path, shift: int, columns: int = 40, rows: int = 400) -> Path:
    """Many numeric columns that drift between ``shift`` 0 and 1: a long change list."""
    rng = random.Random(2)
    with open(path, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow([f"m{i}" for i in range(columns)])
        for _ in range(rows):
            out.writerow([round(rng.gauss(100 + shift * 80, 10)) for _ in range(columns)])
    return path
