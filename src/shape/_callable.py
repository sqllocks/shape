"""Packages that are also public functions: ``shape.profile``, ``shape.query`` and ``shape.diff``.

Each is a subpackage *and* a function of the public API (``shape.profile(source)``). Importing the
subpackage (``import shape.profile.engine``) binds the package to ``shape.<name>``, so the
package itself has to be callable, whichever spelling the caller used and whichever import came
first. ``make_callable`` gives it a ``__call__`` that forwards to ``shape.api`` (imported only
when called, which keeps ``import shape`` cheap).
"""

from __future__ import annotations

import sys
import types
from typing import Any


def make_callable(package: str, api_name: str) -> None:
    def __call__(self: types.ModuleType, *args: Any, **kwargs: Any) -> Any:
        from shape import api

        return getattr(api, api_name)(*args, **kwargs)

    cls = type("CallablePackage", (types.ModuleType,), {"__call__": __call__})
    sys.modules[package].__class__ = cls
