"""Shared by the W1-15 tests: a test-only strategy that exists in two versions, and specs that
use it. The version 1 algorithm is ``row``; version 2 is ``row * 10`` (so the data differ)."""

from __future__ import annotations

import copy
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import numpy as np
import pyarrow as pa

from shape.generation.schema import GenSchema
from shape.plugins.host import default_host, reset_default_host

PROBE = "v2probe"


class TwoVersions:
    """A strategy with generator versions 1 and 2."""

    name = PROBE
    generator_version = 2

    def generate(self, spec: Mapping[str, Any], ctx: Any) -> pa.Array:
        return self.generate_versioned(spec, ctx, 2)

    def generate_versioned(self, spec: Mapping[str, Any], ctx: Any, version: int) -> pa.Array:
        rows = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows, dtype=np.int64)
        return pa.array(rows * (10 if version == 2 else 1))


class OlderRelease(TwoVersions):
    """The same strategy as an older 1.x release had it: only version 1 existed."""

    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: Any) -> pa.Array:
        return self.generate_versioned(spec, ctx, 1)


class OneVersion:
    """A strategy that declares version 2 but cannot run version 1 (no selector)."""

    name = "v2only"
    generator_version = 2

    def generate(self, spec: Mapping[str, Any], ctx: Any) -> pa.Array:
        return pa.array(np.arange(ctx.row_start, ctx.row_start + ctx.n_rows, dtype=np.int64))


class NoVersion:
    """A plugin strategy that declares no ``generator_version`` (it is version 1)."""

    name = "noversion"

    def generate(self, spec: Mapping[str, Any], ctx: Any) -> pa.Array:
        return pa.array(np.zeros(ctx.n_rows, dtype=np.int64))


@contextmanager
def probes(older_release: bool = False) -> Iterator[None]:
    """The test strategies registered in the default plugin host, removed on exit.
    ``older_release`` registers the probe as an older 1.x release had it (version 1 only)."""
    host = default_host()
    host.register("shape.strategies", PROBE, OlderRelease if older_release else TwoVersions)
    host.register("shape.strategies", "v2only", OneVersion)
    host.register("shape.strategies", "noversion", NoVersion)
    try:
        yield
    finally:
        reset_default_host()


def spec_doc(strategy: str = PROBE, rows: int = 20, **extra: Any) -> dict[str, Any]:
    """A one-table spec whose column ``x`` uses ``strategy``."""
    doc: dict[str, Any] = {
        "schema_version": 1,
        "model": {"name": "m", "seed": 5},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["k"],
                "columns": {
                    "k": {"name": "k", "type": "integer", "generator": {"strategy": "sequence"}},
                    "x": {"name": "x", "type": "integer", "generator": {"strategy": strategy}},
                },
            }
        },
        "generation": {"scale": "s", "scales": {"s": {"t": rows}}},
    }
    doc.update(copy.deepcopy(extra))
    return doc


def schema(strategy: str = PROBE, rows: int = 20, **extra: Any) -> GenSchema:
    return GenSchema.from_dict(spec_doc(strategy, rows, **extra))
