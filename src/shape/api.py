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


def generate(
    shape: Any,
    n: Any = None,
    seed: int | None = None,
    relationships: Any = None,
    *,
    scale: str | None = None,
    mode: str | None = None,
) -> Any:
    """Generate data.

    ``generate("retail", scale="medium", seed=42, mode="star")`` runs a domain (or a
    ``GenSchema``, or a generation schema ``dict``) through the engine and returns the
    ``GenerationResult``: ``result.tables`` maps each table name to a ``pyarrow.Table`` (so does
    ``result["order"]``). ``scale`` is a preset name, ``seed`` defaults to the schema's and ``mode``
    (``3nf`` or ``star``) picks a domain's schema.

    The earlier form, ``generate(shape_model, n, seed, relationships)``, still generates rows from
    a Shape model.
    """
    from shape.generation.schema import GenSchema

    if _is_profile(shape):
        from shape.generation.engine import Engine
        from shape.generation.fit import PRESET, fit_schema

        rows = None if n is None else int(n)
        fitted = fit_schema(shape, rows=rows)
        return Engine(fitted.schema, scale=scale or PRESET, seed=seed).generate()
    if isinstance(shape, str):
        from shape.generation.domains import load_domain
        from shape.generation.engine import Engine

        return Engine(load_domain(shape, mode=mode).schema, scale=scale, seed=seed).generate()
    if isinstance(shape, GenSchema) or (isinstance(shape, dict) and "tables" in shape):
        from shape.generation.engine import Engine

        schema = shape if isinstance(shape, GenSchema) else GenSchema.from_dict(shape)
        return Engine(schema, scale=scale, seed=seed).generate()
    from shape.generation import generate_from_shape

    return generate_from_shape(shape, n, 0 if seed is None else seed, relationships)


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

    return certify_shapes(target, observed, **kwargs)


def plan(shape: Any) -> Any:
    from shape.generation.fidelity import plan_reconstruction

    return plan_reconstruction(shape)
