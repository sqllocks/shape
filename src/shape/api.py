"""Small stable public API for Shape."""

from __future__ import annotations

from typing import Any

from shape.contracts.v1 import check as check
from shape.contracts.v1 import diff as diff
from shape.profile.reference import load as load
from shape.profile.reference import profile as profile
from shape.profile.reference import save as save


def _is_profile(obj: Any) -> bool:
    """True for a 0.9 profile (``shape.profile``) or its ``to_dict()``."""
    if type(obj).__module__.startswith("shape.profile.reference"):
        return True
    if isinstance(obj, dict):
        if {"row_count", "columns", "detected_fks"} <= obj.keys():
            return True
        tables = obj.get("tables")
        if isinstance(tables, dict) and tables:
            return all(isinstance(t, dict) and "row_count" in t for t in tables.values())
    return False


def generate(shape: Any, n: Any = None, seed: int = 0, relationships: Any = None) -> Any:
    if _is_profile(shape):
        raise NotImplementedError(
            "generating data from a shape.profile() result is not available yet in this "
            "early-access release; profiling, check and diff are. It is planned (profile to "
            "generate)."
        )
    from shape.generation import generate_from_shape

    return generate_from_shape(shape, n, seed, relationships)


def timeline(versions: Any) -> Any:
    from shape.generation import ShapeTimeline

    return ShapeTimeline(versions)  # type: ignore[no-untyped-call]


def view(shape: Any) -> Any:
    from shape.query import ShapeView

    return ShapeView(shape)


def query(shape: Any, expression: Any) -> Any:
    from shape.query import query as _query

    return _query(shape, expression)


def certify(target: Any, observed: Any, **kwargs: Any) -> Any:
    from shape.generation.fidelity import certify_shapes

    return certify_shapes(target, observed, **kwargs)  # type: ignore[no-untyped-call]


def plan(shape: Any) -> Any:
    from shape.generation.fidelity import plan_reconstruction

    return plan_reconstruction(shape)  # type: ignore[no-untyped-call]
