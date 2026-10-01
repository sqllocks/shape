"""Built-ins as plugins: they register through the same host, under the same entry points.

The entry points in ``pyproject.toml`` are the normal route. :func:`register_builtins` adds any
built-in that discovery did not find (a source tree whose metadata predates the entry points, a
zipped or vendored copy), so the default host always lists them. This is the only module that
may import :mod:`shape.builtins` (an import-linter contract in ``pyproject.toml``).
"""

from __future__ import annotations

import importlib
from collections.abc import Callable
from typing import Any

from shape.builtins.catalog import BUILTINS
from shape.plugins.host import HOST_API, PluginHost

SOURCE = "sqllocks-shape"


def _factory(target: str) -> Callable[[], Any]:
    module, _, attr = target.partition(":")

    def build() -> Any:
        return getattr(importlib.import_module(module), attr)()

    return build


def register_builtins(host: PluginHost) -> int:
    """Register every built-in the host does not already know; return how many were added."""
    added = 0
    for group, name, target in BUILTINS:
        if host.record(group, name) is not None:
            continue
        rec = host.register(group, name, _factory(target), api=HOST_API, source=SOURCE)
        rec.target = target
        added += 1
    return added
