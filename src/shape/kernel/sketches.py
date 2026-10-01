"""Bounded, mergeable sketches (T-14) through the selected kernel.

``Hll`` (p=14), ``Kll`` (k=200) and ``SpaceSaving`` (capacity 64) resolve to the native classes
or their pure-Python twins according to ``SHAPE_KERNEL``. Both have the same API and the same
state for the same input.
"""

from __future__ import annotations

from typing import Any

from .dispatch import get_kernel


def Hll(p: int = 14) -> Any:  # noqa: N802 - class-like factory
    return get_kernel().Hll(p)


def Kll(k: int = 200) -> Any:  # noqa: N802
    return get_kernel().Kll(k)


def SpaceSaving(capacity: int = 64) -> Any:  # noqa: N802
    return get_kernel().SpaceSaving(capacity)
