"""Machine-readable local conformance checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str = ""


def run(checks: dict[str, Callable[[], None]]) -> list[Check]:
    out = []
    for name, fn in checks.items():
        try:
            fn()
            out.append(Check(name, True, ""))
        except Exception as e:
            out.append(Check(name, False, f"{type(e).__name__}: {e}"))
    return out
