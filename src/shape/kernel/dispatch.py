"""Choose between the native kernel and its pure-Python reference twin (T-03).

``SHAPE_KERNEL`` selects the implementation:

* ``auto`` (default): the native ``shape._kernel`` when it can be imported, else the reference;
* ``rust``: the native kernel, or an ``ImportError`` if it is missing;
* ``python``: always the reference.

The choice is made when :func:`get_kernel` is first called and is cached; call
:func:`reset` (tests) to choose again after changing the environment variable.
"""

from __future__ import annotations

import importlib
import os
from functools import lru_cache
from types import ModuleType

ENV_VAR = "SHAPE_KERNEL"
MODES = ("auto", "rust", "python")


def mode() -> str:
    """The requested mode, validated."""
    value = os.environ.get(ENV_VAR, "auto").strip().lower() or "auto"
    if value not in MODES:
        raise ValueError(f"{ENV_VAR} must be one of {', '.join(MODES)}; got {value!r}")
    return value


def _import_native() -> ModuleType:
    return importlib.import_module("shape._kernel")


@lru_cache(maxsize=1)
def get_kernel() -> ModuleType:
    """The selected kernel module. It has a ``NAME`` attribute: ``"rust"`` or ``"python"``."""
    requested = mode()
    if requested != "python":
        try:
            native = _import_native()
        except ImportError as exc:
            if requested == "rust":
                raise ImportError(
                    f"{ENV_VAR}=rust but the native extension shape._kernel is not "
                    "available (pure-Python install?)"
                ) from exc
        else:
            return native
    from . import reference

    return reference


def reset() -> None:
    """Forget the cached choice (used by tests that change ``SHAPE_KERNEL``)."""
    get_kernel.cache_clear()


def kernel_name() -> str:
    """``"rust"`` or ``"python"``: which implementation is active."""
    return str(get_kernel().NAME)
