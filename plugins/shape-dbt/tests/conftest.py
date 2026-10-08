from __future__ import annotations

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
JAFFLE = ROOT / "examples" / "dbt_jaffle_shop"


@pytest.fixture
def jaffle() -> Path:
    return JAFFLE
