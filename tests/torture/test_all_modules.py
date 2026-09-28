import importlib
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODS = json.loads((ROOT / "rq/torture_inventory.json").read_text())["modules"]


@pytest.mark.parametrize("name", MODS)
def test_every_implementation_module_imports(name):
    m = importlib.import_module(name)
    assert m is not None


@pytest.mark.parametrize("name", MODS)
def test_public_objects_are_introspectable(name):
    m = importlib.import_module(name)
    for n in getattr(m, "__all__", ()):
        assert hasattr(m, n)
