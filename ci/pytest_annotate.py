"""pytest plugin for CI: each failing test becomes a GitHub Actions error annotation.

The job logs are not always at hand, but annotations are readable from the check run. Loaded only
by .github/workflows/ci.yml (pytest -p pytest_annotate, PYTHONPATH: ci); it changes no
result. GitHub keeps at most 10 error annotations per step.
"""

from __future__ import annotations

import os
from typing import Any


def _clean(text: str) -> str:
    return text.replace("%", "%25").replace("\r", "").replace("\n", "%0A")


def pytest_runtest_logreport(report: Any) -> None:
    if not os.environ.get("GITHUB_ACTIONS") or not report.failed:
        return
    path, line, _ = report.location
    crash = getattr(getattr(report, "longrepr", None), "reprcrash", None)
    lines = report.longreprtext.strip().splitlines()
    message = (
        [crash.message.splitlines()[0]] if crash and crash.message else lines[-1:] or ["failed"]
    )
    print(
        f"\n::error file={path},line={(line or 0) + 1},title={_clean(report.nodeid)[:200]}"
        f"::{_clean(report.when + ': ' + message[0])[:500]}",
        flush=True,
    )


def pytest_collectreport(report: Any) -> None:
    """A module that fails to import (collection error) is an annotation too."""
    if not os.environ.get("GITHUB_ACTIONS") or not report.failed:
        return
    lines = report.longreprtext.strip().splitlines()
    print(
        f"\n::error file={report.nodeid or '.'},title=collection {_clean(report.nodeid)[:200]}"
        f"::{_clean(lines[-1] if lines else 'collection failed')[:500]}",
        flush=True,
    )


def pytest_sessionfinish(session: Any, exitstatus: int) -> None:
    """A run that fails without a failing test (usage error, no tests collected, a crash in a
    fixture outside the test phases) still says how it ended."""
    if os.environ.get("GITHUB_ACTIONS") and int(exitstatus) not in (0, 1):
        print(
            f"\n::error title=pytest exit {int(exitstatus)}::pytest ended with exit status "
            f"{int(exitstatus)} ({session.testsfailed} failed)",
            flush=True,
        )
