"""Test-only import scaffolding for pure helpers in historical harnesses.

FRZ-01 leaves these harness files unchanged. Any attempt to use the archived
interpreter or package still raises: these imports cannot run the engine.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

MESSAGE = "the reference engine is archived (FRZ-01, 2026-10-11); this harness is historical"


class Unavailable:
    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(MESSAGE)

    def __str__(self) -> str:
        raise RuntimeError(MESSAGE)

    def __fspath__(self) -> str:
        raise RuntimeError(MESSAGE)


@contextmanager
def historical_imports() -> Iterator[None]:
    bench = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_refengine"
    spec = importlib.util.spec_from_file_location("frz_test_paths", bench / "paths.py")
    assert spec and spec.loader
    paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paths)
    for name in ("REFENGINE_PY", "REFENGINE_ROOT", "REFENGINE_VENV"):
        setattr(paths, name, Unavailable())
    package = ModuleType("_refpkg")
    package.__getattr__ = Unavailable().__getattr__
    replacements = {"paths": paths, "_refpkg": package}
    saved = {name: sys.modules.get(name) for name in replacements}
    sys.modules.update(replacements)
    try:
        yield
    finally:
        for name, previous in saved.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
