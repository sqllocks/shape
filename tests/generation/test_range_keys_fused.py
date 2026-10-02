"""Sequence columns and the uniform / Zipf foreign keys that point at a sequence key come from one
native call each; the tables are those of the step-by-step path, whichever kernel runs."""

from __future__ import annotations

import pytest

from shape.builtins.strategies import keys as keys_module
from shape.generation import kernel_ops
from shape.generation.domains import load_domain
from shape.generation.engine import Engine


def _tables(domain: str, **kw):
    return Engine(load_domain(domain).schema, scale="small", seed=1042, **kw).generate().tables


@pytest.mark.parametrize("domain", ["hr", "insurance", "retail"])
def test_fused_keys_and_sequences_give_the_same_tables(domain, monkeypatch):
    fused = _tables(domain)
    with monkeypatch.context() as m:
        m.setattr(keys_module.ForeignKey, "_range_keys", staticmethod(lambda *a, **k: None))
        m.setattr(
            kernel_ops,
            "range_values",
            lambda start, step, row_start, n: _numpy_sequence(start, step, row_start, n),
        )
        plain = _tables(domain)
    assert fused.keys() == plain.keys()
    for name in fused:
        assert fused[name].equals(plain[name]), name


def _numpy_sequence(start, step, row_start, n):
    import numpy as np

    from shape.generation.arrowkit import array as arrow_array

    index = np.arange(row_start, row_start + n, dtype=np.int64)
    return arrow_array(start + index * step)


def test_chunking_does_not_change_the_fused_keys():
    whole = _tables("hr")
    chunked = _tables("hr", chunk_rows=700)
    for name in whole:
        assert whole[name].equals(chunked[name]), name
