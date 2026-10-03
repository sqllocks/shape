"""Shape plugin: behavior models (state machines on a virtual clock).

The public names are importable from here, lazily: importing the package loads no module of
this plugin beyond this file, and none of NumPy or Arrow. See ``docs/plugins/behavior.md``.
"""

from __future__ import annotations

import importlib
from typing import Any

SHAPE_API = "1.0"

_EXPORTS = {
    "Module": "shape_behavior.model",
    "ModuleError": "shape_behavior.model",
    "load_module": "shape_behavior.model",
    "Population": "shape_behavior.population",
    "SimConfig": "shape_behavior.simulator",
    "Simulator": "shape_behavior.simulator",
    "Checkpoint": "shape_behavior.simulator",
    "EVENT_SCHEMA": "shape_behavior.events",
    "import_gmf": "shape_behavior.gmf",
    "ImportResult": "shape_behavior.gmf",
    "Unsupported": "shape_behavior.gmf",
    "UnsupportedGmfError": "shape_behavior.gmf",
    "register_state_type": "shape_behavior.extension",
    "StateHandler": "shape_behavior.extension",
    "StateContext": "shape_behavior.extension",
    "Emission": "shape_behavior.extension",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'shape_behavior' has no attribute {name!r}")
    return getattr(importlib.import_module(module), name)
