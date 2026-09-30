"""Small stable public API for Shape."""

from __future__ import annotations

import importlib
import types
from typing import Any

from shape.contracts.v1 import check as check
from shape.contracts.v1 import diff as diff
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


class _CallableModule(types.ModuleType):
    """Makes the legacy ``shape.diff`` subpackage callable.

    ``shape.diff`` is both this API function and an old subpackage. Importing the subpackage
    (``import shape.diff``) rebinds the attribute ``shape.diff`` to the module, which would
    break ``shape.diff(a, b)``. Giving that module a ``__call__`` keeps the call working
    either way. The clash goes away when the legacy package is cut (plan P0-04).
    """

    def __call__(self, baseline: Any, current: Any, **kwargs: Any) -> Any:
        return diff(baseline, current, **kwargs)


importlib.import_module("shape.diff").__class__ = _CallableModule
