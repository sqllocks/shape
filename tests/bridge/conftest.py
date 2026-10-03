"""Fixtures of the bridge tests."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scale"))

from bridge_helpers import ROWS, Caller, Caller11, write_csv  # noqa: E402

from shape.bridge.core import Bridge  # noqa: E402


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


@pytest.fixture
def api(bridge) -> Caller:
    return Caller(bridge)


@pytest.fixture
def api11(bridge) -> Caller11:
    return Caller11(bridge)


@pytest.fixture
def schema_file(tmp_path) -> Path:
    from scale_schemas import plain_doc

    path = tmp_path / "schema.json"
    path.write_text(json.dumps(plain_doc(ROWS)))
    return path


@pytest.fixture
def csv_pair(tmp_path) -> tuple[Path, Path]:
    return write_csv(tmp_path / "a.csv"), write_csv(tmp_path / "b.csv", shift=1)
