"""Historical harness imports fail with the precise retirement explanation."""

import importlib.util
from pathlib import Path

import pytest


def test_reference_package_raises_exact_archival_error_on_import() -> None:
    path = Path(__file__).resolve().parents[2] / "benchmarks/regression/_refpkg.py"
    spec = importlib.util.spec_from_file_location("frz_actual_archival_guard", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    with pytest.raises(RuntimeError) as caught:
        spec.loader.exec_module(module)
    assert type(caught.value) is RuntimeError
    assert str(caught.value) == (
        "the reference engine is archived (FRZ-01, 2026-10-11); this harness is historical"
    )
