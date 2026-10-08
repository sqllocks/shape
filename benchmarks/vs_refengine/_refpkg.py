"""Names the reference engine itself defines, derived from one setting: ``REFENGINE_NAME``.

``REFENGINE_NAME`` is the reference engine's short name, set outside the repository (see
``scripts/env.sh``). Everything the engine names at run time comes from it: its import package,
its generator class, its installed console script, the prefix of the fields its stream events
carry, its default output directory and its git URL. Nothing in the repository spells them out.

Standard library only: the module is imported from both the RefEngine venv and the Shape venv.
The values are read when first used, so importing this module never fails; using one without
``REFENGINE_NAME`` set fails with a message that says what to set.
"""

from __future__ import annotations

import importlib
import os
from types import ModuleType

_DERIVED = {
    "NAME": lambda n: n,
    "PACKAGE": lambda n: f"sqllocks_{n}",  # import root of the engine
    "DIST": lambda n: f"sqllocks-{n}",  # its distribution (pip) name
    "ENGINE_CLASS": lambda n: n.capitalize(),  # the generator class in engine.generator
    "CONSOLE": lambda n: n,  # the installed console script, REFENGINE_VENV/bin/CONSOLE
    "FIELD_PREFIX": lambda n: f"_{n}_",  # stream event fields: <prefix>table, <prefix>seq, ...
    "OUTPUT_DIR": lambda n: f"{n}_output",  # the engine's default output directory
    "REPO_URL": lambda n: f"https://github.com/sqllocks/{n}",
}


def _name() -> str:
    name = os.environ.get("REFENGINE_NAME", "").strip()
    if not name:
        raise RuntimeError(
            "REFENGINE_NAME is not set: export the reference engine's short name "
            "(set outside the repository; see scripts/env.sh)"
        )
    return name


def __getattr__(attr: str) -> str:
    if attr in _DERIVED:
        return _DERIVED[attr](_name())
    raise AttributeError(f"module {__name__!r} has no attribute {attr!r}")


def mod(sub: str = "") -> ModuleType:
    """The engine's module ``sub`` (dotted, relative to its package; "" for the package)."""
    package = _DERIVED["PACKAGE"](_name())
    return importlib.import_module(f"{package}.{sub}" if sub else package)
