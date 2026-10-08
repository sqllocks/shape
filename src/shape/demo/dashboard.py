"""``ProgressDashboard``: the steps of a run, printed as they happen."""

from __future__ import annotations

import sys
import time
from enum import Enum
from typing import TextIO


class DemoStep(Enum):
    CONNECTING = "Connecting to targets"
    PROFILING = "Profiling source data"
    GENERATING = "Generating synthetic data"
    COMPARING = "Comparing distributions"
    WRITING = "Writing to targets"
    NOTEBOOK = "Generating notebook"
    DONE = "Complete"
    FAILED = "Failed"


class ProgressDashboard:
    """Plain text on ``out`` (standard output by default; the bridge passes standard error so its
    JSON answer stays alone on standard output)."""

    def __init__(
        self, scenario: str, mode: str, total_rows: int = 0, out: TextIO | None = None
    ) -> None:
        self._scenario = scenario
        self._mode = mode
        self._total_rows = total_rows
        self._out = out
        self._start_time = time.time()

    def _print(self, text: str) -> None:
        print(text, file=self._out or sys.stdout)

    def start(self) -> None:
        self._print(f"=== Shape Demo — {self._scenario} ({self._mode}) ===")

    def step(self, s: DemoStep, detail: str = "") -> None:
        elapsed = time.time() - self._start_time
        msg = f"[{elapsed:.1f}s] {s.value}"
        if detail:
            msg += f": {detail}"
        self._print(f"  >> {msg}")

    def info(self, message: str) -> None:
        self._print(f"     {message}")

    def finish(self, success: bool, error: str | None = None) -> None:
        elapsed = time.time() - self._start_time
        if success:
            self._print(f"=== Done in {elapsed:.1f}s ===")
        else:
            self._print(f"=== ERROR: Failed after {elapsed:.1f}s: {error or 'unknown error'} ===")
