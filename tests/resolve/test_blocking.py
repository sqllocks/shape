"""W3-09 (#72): blocking by key, prefix, phonetic code and n-gram."""

from __future__ import annotations

import numpy as np
import pyarrow as pa
import pytest

from shape.resolve.blocking import BlockRule, block_keys, candidate_pairs


def table() -> pa.Table:
    return pa.table(
        {
            "name": ["Robert Smith", "Rupert Smith", "Ann Lee", "Anne Lee", None, "Bob Stone"],
            "zip": ["10001", "10001", "20002", "20002", "10001", None],
        }
    )


def test_key_blocks_pair_equal_normalized_values_only() -> None:
    pairs, stats = candidate_pairs(table(), [BlockRule("zip", "key")])
    assert pairs.tolist() == [[0, 1], [0, 4], [1, 4], [2, 3]]
    assert stats["pairs"] == 4


def test_prefix_phonetic_and_ngram_blocks() -> None:
    t = table()
    assert block_keys(t["name"].to_pylist(), BlockRule("name", "prefix", 3))[0] == ["rob"]
    ph = block_keys(t["name"].to_pylist(), BlockRule("name", "phonetic"))
    assert ph[0] == ph[1] and ph[4] == []  # Robert/Rupert sound alike; null has no key
    ng = block_keys(["abcd"], BlockRule("name", "ngram", 3))[0]
    assert sorted(ng) == ["abc", "bcd"]
    pairs, _ = candidate_pairs(t, [BlockRule("name", "phonetic")])
    assert [0, 1] in pairs.tolist() and [2, 3] in pairs.tolist()


def test_several_rules_are_a_sorted_unique_union() -> None:
    pairs, stats = candidate_pairs(
        table(), [BlockRule("zip", "key"), BlockRule("name", "prefix", 3)]
    )
    got = pairs.tolist()
    assert got == sorted(got) and len({tuple(p) for p in got}) == len(got)
    assert all(i < j for i, j in got)
    assert stats["rules"] == 2


def test_multi_column_rule_needs_every_part() -> None:
    t = pa.table({"a": ["x", "x", "x", None], "b": ["1", "1", "2", "1"]})
    pairs, _ = candidate_pairs(t, [BlockRule(("a", "b"), "key")])
    assert pairs.tolist() == [[0, 1]]


def test_oversize_blocks_are_skipped_and_reported() -> None:
    t = pa.table({"c": ["same"] * 50 + ["u", "u"]})
    pairs, stats = candidate_pairs(t, [BlockRule("c", "key")], max_block=10)
    assert pairs.tolist() == [[50, 51]]
    assert stats["skipped_blocks"] == 1 and stats["skipped_records"] == 50


def test_empty_and_unknown_inputs() -> None:
    pairs, stats = candidate_pairs(
        pa.table({"c": pa.array([], pa.string())}), [BlockRule("c", "key")]
    )
    assert pairs.shape == (0, 2) and stats["pairs"] == 0
    with pytest.raises(ValueError, match="no column"):
        candidate_pairs(table(), [BlockRule("nope", "key")])
    with pytest.raises(ValueError, match="unknown blocking method"):
        BlockRule("name", "bogus")
    with pytest.raises(ValueError, match="at least one"):
        candidate_pairs(table(), [])
    with pytest.raises(ValueError, match="size"):
        BlockRule("name", "prefix", 0)
    assert isinstance(np.zeros(1), np.ndarray)
