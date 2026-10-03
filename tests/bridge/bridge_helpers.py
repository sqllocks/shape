"""Helpers of the bridge tests (a module of its own: ``from conftest import ...`` would find
another directory's conftest in a full run)."""

from __future__ import annotations

import csv
import json
import random
from pathlib import Path
from typing import Any

from shape.bridge.core import Bridge

ROWS = {"customer": 40, "order": 1200, "order_line": 3100}


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


def write_wide(path: Path, shift: int, columns: int = 40, rows: int = 400) -> Path:
    """Many numeric columns that drift between ``shift`` 0 and 1: a long change list."""
    rng = random.Random(2)
    with open(path, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow([f"m{i}" for i in range(columns)])
        for _ in range(rows):
            out.writerow([round(rng.gauss(100 + shift * 80, 10)) for _ in range(columns)])
    return path
