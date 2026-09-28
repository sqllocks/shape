"""Small stable public API for Shape RC1."""

from __future__ import annotations

from shape.artifact import read_shape, write_shape
from shape.capture import capture_columns, capture_rows
from shape.drift import compare


def profile(data):
    if isinstance(data, dict):
        return capture_columns(data)
    return capture_rows(data).to_dict()


def save(shape, path, **kwargs):
    return write_shape(path, shape, **kwargs)


def load(path):
    return read_shape(path)[1]


def diff(before, after):
    return compare(before, after)


def generate(shape, n=None, seed=0, relationships=None):
    from shape.generation import generate_from_shape

    return generate_from_shape(shape, n, seed, relationships)


def timeline(versions):
    from shape.generation import ShapeTimeline

    return ShapeTimeline(versions)


def view(shape):
    from shape.query import ShapeView

    return ShapeView(shape)


def query(shape, expression):
    from shape.query import query as _query

    return _query(shape, expression)


def certify(target, observed, **kwargs):
    from shape.generation.fidelity import certify_shapes

    return certify_shapes(target, observed, **kwargs)


def plan(shape):
    from shape.generation.fidelity import plan_reconstruction

    return plan_reconstruction(shape)


def check(shape, contract):
    from shape.contracts import evaluate_contract

    return evaluate_contract(shape, contract)
