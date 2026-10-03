"""Counter-based random draws: each is a pure function of its key, never of iteration order.

``uniform(seed, entity, stream, k)`` hashes the four integers with the SplitMix64 finalizer, one
round per component, and keeps the top 53 bits. The same key always gives the same number, so a
run does not depend on how entities are batched, how many times it is resumed or what else was
simulated alongside. Python's ``hash()`` and global random state are never used.
"""

from __future__ import annotations

import hashlib
from typing import Any

import numpy as np

_C1 = np.uint64(0x9E3779B97F4A7C15)
_C2 = np.uint64(0xBF58476D1CE4E5B9)
_C3 = np.uint64(0x94D049BB133111EB)
_S30, _S27, _S31, _S11 = np.uint64(30), np.uint64(27), np.uint64(31), np.uint64(11)
_MASK = (1 << 64) - 1
_SCALE = 1.0 / (1 << 53)
INIT_MODULE = 0xFFFF  # module slot of the draws made when entities are created


def _mix(x: Any) -> Any:
    x = (x ^ (x >> _S30)) * _C2
    x = (x ^ (x >> _S27)) * _C3
    return x ^ (x >> _S31)


def stream_id(module: int, step: Any) -> Any:
    """The stream of a module instance's ``step``-th state entry (``step`` below 2**40)."""
    return (np.uint64(module) << np.uint64(40)) | np.asarray(step).astype(np.uint64)


def name_key(name: str) -> int:
    """A stable 40-bit key for a name (attribute names, population settings)."""
    return int.from_bytes(hashlib.blake2b(name.encode(), digest_size=5).digest(), "big")


def uniform(seed: int, entity: Any, stream: Any, k: int) -> Any:
    """Uniform draws in [0, 1), one per element of ``entity`` (an int64 array)."""
    with np.errstate(over="ignore"):
        h = _mix(np.asarray(entity).astype(np.uint64) * _C1 + np.uint64(seed & _MASK))
        h = _mix(h ^ (np.asarray(stream).astype(np.uint64) * _C1 + _C3))
        h = _mix(h ^ (np.uint64(k + 1) * _C2))
        return (h >> _S11).astype(np.float64) * _SCALE
