import importlib
import pkgutil

import pytest

import shape

MODS = sorted(m.name for m in pkgutil.walk_packages(shape.__path__, "shape."))

# Importing these, or resolving their lazy public names, needs the optional
# `cryptography` package ([sign] extra).
NEEDS_SIGN_IMPORT = {"shape.security.crypto"}
NEEDS_SIGN_NAMES = {"shape.security", "shape.security.crypto"}


def _params(needs_sign):
    return [pytest.param(m, marks=pytest.mark.sign) if m in needs_sign else m for m in MODS]


@pytest.mark.parametrize("name", _params(NEEDS_SIGN_IMPORT))
def test_every_implementation_module_imports(name):
    m = importlib.import_module(name)
    assert m is not None


@pytest.mark.parametrize("name", _params(NEEDS_SIGN_NAMES))
def test_public_objects_are_introspectable(name):
    m = importlib.import_module(name)
    for n in getattr(m, "__all__", ()):
        assert hasattr(m, n)
