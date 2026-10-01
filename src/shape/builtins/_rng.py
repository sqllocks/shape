"""The random stream of one generation chunk, keyed so the result is deterministic."""

from __future__ import annotations

import hashlib

import numpy as np

from shape.plugins.api.v1 import GenerationContext


def chunk_generator(ctx: GenerationContext) -> np.random.Generator:
    """A Philox stream keyed by ``(seed, table, column, chunk)``: the same context always gives
    the same numbers, and no two chunks or columns share a stream."""
    key = f"{ctx.seed}\x00{ctx.table}\x00{ctx.column}\x00{ctx.chunk}".encode()
    digest = hashlib.blake2b(key, digest_size=16).digest()
    return np.random.Generator(np.random.Philox(key=int.from_bytes(digest, "little")))
