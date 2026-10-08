from __future__ import annotations

import pytest

from shape.cli.generation import load_target
from shape.generation.engine import Engine


@pytest.fixture(scope="module")
def retail_engine() -> Engine:
    return Engine(load_target("retail"), scale="small", seed=11)


def make_engine(seed: int = 11) -> Engine:
    return Engine(load_target("retail"), scale="small", seed=seed)
