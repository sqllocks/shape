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


@pytest.mark.parametrize("name", [n for n in REMOVED if n not in ("integrations", "history")])
def test_removed_module_is_not_importable(name):
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module(f"shape.{name}")


def test_legacy_integrations_stay_removed():
    """PF-03 re-created ``shape.integrations`` for ``fabric`` only: nothing from the P0-04
    module (plan section 8.1, item 1) may come back under it."""
    import pkgutil

    import shape.integrations as pkg

    assert {m.name for m in pkgutil.iter_modules(pkg.__path__)} == {"fabric"}
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("shape.integrations.etl")


def test_legacy_history_stays_removed():
    """W3-03 re-created ``shape.history`` as the bisect/timelapse API (issue #101, item 5):
    nothing from the P0-04 legacy module (plan section 8.1) may come back under it."""
    import pkgutil

    import shape.history as pkg

    assert {m.name for m in pkgutil.iter_modules(pkg.__path__)} == {
        "_common",
        "_page",
        "bisect",
        "layers",
        "timelapse",
        "versions",
    }
    for sub in ("core", "dag", "series"):
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"shape.history.{sub}")
    for name in (
        "HistoryEntry",
        "ShapeDelta",
        "LocalHistory",
        "HistoryDAG",
        "Revision",
        "ShapeSeries",
    ):
        assert not hasattr(pkg, name)
