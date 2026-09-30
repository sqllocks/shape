"""P0-04: the modules cut in plan section 8.1 are gone (SEC6-SEC8, RM1-RM8)."""

import importlib

import pytest

REMOVED = [
    "integrations",
    "etl",
    "ci",
    "distributed",
    "admin",
    "ai",
    "marketplace",
    "federation",
    "enterprise",
    "governance",
    "graph",
    "compiler",
    "execution",
    "reproducibility",
    "explain",
    "policy",
    "history",
    "lineage",
    "observability",
    "packages",
    "reference",
    "scenarios",
    "temporal",
    "testing",
    "transform",
    "hub",
    "webapp",
]


def test_twenty_seven_modules_listed():
    assert len(REMOVED) == len(set(REMOVED)) == 27


@pytest.mark.parametrize("name", REMOVED)
def test_removed_module_is_not_importable(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"shape.{name}")
