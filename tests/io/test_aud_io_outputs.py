"""AUD-io: regression tests for the output side of shape.io (multi_store, landing, store)."""

from __future__ import annotations

import datetime as dt

import pytest

from shape.io.multi_store import MultiStoreWriter


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name

    def write_all(self, tables, **kwargs):
        return self.name


def test_501_labels_stay_unique_when_a_writer_is_named_like_a_generated_label():
    writers = [_Named("a"), _Named("a#2"), _Named("a")]
    result = MultiStoreWriter(writers).write_all({})
    assert len(result.results) == 3
    assert sorted(result.results.values()) == ["a", "a", "a#2"]
    assert MultiStoreWriter([_Named("x"), _Named("x")]).write_all({}).results == {
        "x": "x",
        "x#2": "x",
    }
