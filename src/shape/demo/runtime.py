"""``DemoRuntime``: what a demo run needs from outside, in one place.

The defaults are the real thing. A caller (the command line, the JSON bridge, a notebook, a test)
passes its own values to change where messages go, where state is kept, or to replace the network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from shape.demo.connections import ConnectionRegistry


@dataclass
class DemoRuntime:
    out: TextIO | None = None  # progress and reports (default: standard output)
    registry: ConnectionRegistry | None = None  # connection profiles (default: the user's)
    manifest_dir: Path | None = None  # session records (default: <SHAPE_HOME>/sessions)
    services: Any = None  # remote operations (:class:`shape.demo.services.FabricServices`)
    sink_factory: Callable[[str, Mapping[str, Any]], Any] | None = None  # a sink by name, or None
    transport: Any = None  # the Fabric REST API (Spark runs, job status)
    token: str | None = None  # a Fabric API bearer token (default: the environment, or sign-in)
    storage_token: str | None = None  # a OneLake (storage) token, with ``token``
    profile_database: Callable[..., Any] | None = None  # the database profiler
    jobs: Any = None  # the job store of Spark runs

    def reg(self) -> ConnectionRegistry:
        if self.registry is None:
            self.registry = ConnectionRegistry()
        return self.registry
