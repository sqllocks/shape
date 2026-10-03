"""``shape demo``: one-command demos of Shape for talks, clients and workshops.

A *scenario* (``retail``, ``adventureworks``, ``healthcare``, ``enterprise``) runs in one of
three modes: ``inference`` (learn from data, generate synthetic data, compare), ``seeding``
(generate and write to a folder, a Lakehouse, a Warehouse, a SQL database or an Eventhouse) and
``streaming`` (stream one table's events). Every run is recorded as a session that can be
reported on and cleaned up.

The operations are plain functions (:mod:`shape.demo.api`) that ``shape demo`` and the JSON bridge
both call. Nothing heavy loads until one runs.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORTS = {
    "demo_cleanup": "api",
    "demo_init": "api",
    "demo_list": "api",
    "demo_notebook": "api",
    "demo_preflight": "api",
    "demo_report": "api",
    "demo_run": "api",
    "demo_status": "api",
    "DemoError": "errors",
    "SessionNotFoundError": "errors",
    "DemoParams": "params",
    "DemoRuntime": "runtime",
    "DemoManifest": "manifest",
    "ConnectionProfile": "connections",
    "ConnectionRegistry": "connections",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module 'shape.demo' has no attribute {name!r}")
    return getattr(import_module(f"{__name__}.{module}"), name)
