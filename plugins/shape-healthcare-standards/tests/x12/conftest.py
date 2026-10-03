from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from shape_healthcare_standards.testing import sample_tables  # noqa: E402


@pytest.fixture
def tables():
    return sample_tables()
