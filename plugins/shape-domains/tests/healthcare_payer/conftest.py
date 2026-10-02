"""One generated population shared by the acceptance tests (generation takes tens of seconds)."""

from __future__ import annotations

import pytest
from shape_domains.healthcare_payer.generate import HealthcarePayerData, generate

POPULATION = 3000
SEED = 21


@pytest.fixture(scope="session")
def data() -> HealthcarePayerData:
    return generate(POPULATION, seed=SEED)


@pytest.fixture(scope="session")
def small() -> HealthcarePayerData:
    return generate(300, seed=5)
