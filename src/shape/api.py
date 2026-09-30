"""Small stable public API for Shape."""

from __future__ import annotations

from typing import Any

from shape.drift import compare
from shape.profile.reference import load as load
from shape.profile.reference import profile as profile
from shape.profile.reference import save as save


def generate(shape: Any, n: Any = None, seed: int = 0, relationships: Any = None) -> Any:
    from shape.generation import generate_from_shape

    return generate_from_shape(shape, n, seed, relationships)


def timeline(versions: Any) -> Any:
    from shape.generation import ShapeTimeline

    return ShapeTimeline(versions)  # type: ignore[no-untyped-call]


def view(shape: Any) -> Any:
    from shape.query import ShapeView

    return ShapeView(shape)  # type: ignore[no-untyped-call]


def query(shape: Any, expression: Any) -> Any:
    from shape.query import query as _query

    return _query(shape, expression)  # type: ignore[no-untyped-call]


def certify(target: Any, observed: Any, **kwargs: Any) -> Any:
    from shape.generation.fidelity import certify_shapes

    return certify_shapes(target, observed, **kwargs)  # type: ignore[no-untyped-call]


def plan(shape: Any) -> Any:
    from shape.generation.fidelity import plan_reconstruction

    return plan_reconstruction(shape)  # type: ignore[no-untyped-call]


def diff(before: Any, after: Any) -> Any:
    return compare(before, after)


def check(shape: Any, contract: Any) -> Any:
    from shape.contracts import evaluate_contract

    return evaluate_contract(shape, contract)
