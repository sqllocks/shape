import importlib
import pkgutil

import pytest

import shape

MODS = sorted(m.name for m in pkgutil.walk_packages(shape.__path__, "shape."))


@pytest.mark.parametrize("name", MODS)
def test_every_implementation_module_imports(name):
    m = importlib.import_module(name)
    assert m is not None


@pytest.mark.parametrize("name", MODS)
def test_public_objects_are_introspectable(name):
    m = importlib.import_module(name)
    for n in getattr(m, "__all__", ()):
        assert hasattr(m, n)
