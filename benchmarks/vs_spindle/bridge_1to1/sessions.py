"""Two JSON-lines sessions: the pinned baseline's persistent bridge and ``shape bridge``.

Standard library only (imported by ``verify.py`` in either venv). Both are real processes spoken to
over standard input and output, the way a client speaks to them."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from paths import SHAPE_PY, SPINDLE_PY  # noqa: E402


class Session:
    """A child process that answers one JSON line with one JSON line."""

    def __init__(self, argv: list[str], env: dict[str, str] | None = None) -> None:
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            env={**os.environ, **(env or {})},
        )

    def ask(self, request: dict[str, Any]) -> dict[str, Any]:
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError(f"{self.proc.args!r} ended without answering {request!r}")
        doc: dict[str, Any] = json.loads(line)
        return doc

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        try:
            self.proc.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self.proc.kill()


class Baseline(Session):
    """The baseline's persistent bridge (``mcp_bridge_server``): the commands run in one process,
    so a stream or a job can be asked about after it starts."""

    def __init__(self) -> None:
        super().__init__([str(SPINDLE_PY), "-m", "sqllocks_spindle.mcp_bridge_server"])
        assert self.proc.stdout is not None
        ready = json.loads(self.proc.stdout.readline())
        if ready.get("data", {}).get("ready") is not True:
            raise RuntimeError(f"the baseline bridge did not start: {ready}")

    def call(self, command: str, **params: Any) -> tuple[bool, Any]:
        """``(ok, data)``: ``ok`` is the protocol status; ``data`` the result or the message."""
        reply = self.ask({"command": command, "params": params})
        if reply["status"] == "ok":
            return True, reply["data"]
        return False, reply["error"]


class Shape(Session):
    def __init__(self, jobs_dir: Path) -> None:
        super().__init__([str(SHAPE_PY), "-m", "shape", "bridge", "--jobs-dir", str(jobs_dir)])
        self.last: dict[str, Any] = {}

    def call(
        self, command: str, options: dict[str, Any] | None = None, **args: Any
    ) -> tuple[bool, Any]:
        """``(ok, result)`` or ``(False, error object)``; the whole response is in ``last``."""
        request: dict[str, Any] = {"api_version": "1.0", "command": command, "args": args}
        if options:
            request["options"] = options
        self.last = self.ask(request)
        return (True, self.last["result"]) if self.last["ok"] else (False, self.last["error"])
