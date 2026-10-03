"""Blocking: cut the n x n comparisons down to the pairs that share a block.

A rule turns the value of one column (or several) into block keys; two rows are candidates when
they share a key under any rule. Methods: ``key`` (the normalized value), ``prefix`` (its first
``size`` characters), ``phonetic`` (Soundex codes of its words) and ``ngram`` (every character
``size``-gram; the frequent ones fall in oversize blocks and are skipped, so a typo only costs the
grams it touches). A block larger than ``max_block`` is skipped and counted, never silently
truncated. A row with a missing value has no key under that rule.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.resolve.distances import normalize_text, phonetic_key

METHODS = ("key", "prefix", "phonetic", "ngram")
_DEFAULT_SIZE = {"prefix": 3, "ngram": 3}


@dataclass(frozen=True)
class BlockRule:
    """One blocking rule.

    Args:
        column: The column, or a tuple of columns that must all be present (their values are
            joined).
        method: One of :data:`METHODS`.
        size: The prefix length or the n-gram size (default 3); unused by ``key`` and ``phonetic``.
    """

    column: str | tuple[str, ...]
    method: str = "key"
    size: int | None = None

    def __post_init__(self) -> None:
        if self.method not in METHODS:
            raise ValueError(
                f"unknown blocking method {self.method!r}; choose one of {', '.join(METHODS)}"
            )
        if isinstance(self.column, list):
            object.__setattr__(self, "column", tuple(self.column))
        if self.size is not None and self.size < 1:
            raise ValueError("the size of a blocking rule must be at least 1")

    @property
    def columns(self) -> tuple[str, ...]:
        return (self.column,) if isinstance(self.column, str) else tuple(self.column)

    @property
    def effective_size(self) -> int:
        return self.size if self.size is not None else _DEFAULT_SIZE.get(self.method, 0)

    def label(self) -> str:
        suffix = f":{self.effective_size}" if self.method in _DEFAULT_SIZE else ""
        return f"{'+'.join(self.columns)}:{self.method}{suffix}"

    def to_dict(self) -> dict[str, object]:
        col: object = self.column if isinstance(self.column, str) else list(self.column)
        out: dict[str, object] = {"column": col, "method": self.method}
        if self.size is not None:
            out["size"] = self.size
        return out


def block_keys(values: list[object], rule: BlockRule) -> list[list[str]]:
    """The block keys of each value under ``rule`` (an empty list for a missing value)."""
    out: list[list[str]] = []
    size = rule.effective_size
    for v in values:
        text = normalize_text(v) if v is not None else ""
        if not text:
            out.append([])
        elif rule.method == "key":
            out.append([text])
        elif rule.method == "prefix":
            out.append([text.replace(" ", "")[:size]])
        elif rule.method == "phonetic":
            key = phonetic_key(text)
            out.append([key] if key else [])
        else:
            if len(text) <= size:
                out.append([text])
            else:
                out.append(sorted({text[i : i + size] for i in range(len(text) - size + 1)}))
    return out


def _rule_values(table: pa.Table, rule: BlockRule) -> list[object]:
    cols = []
    for name in rule.columns:
        if name not in table.column_names:
            raise ValueError(f"blocking rule {rule.label()}: no column {name!r}")
        cols.append(table.column(name).to_pylist())
    if len(cols) == 1:
        return list(cols[0])
    joined: list[object] = []
    for parts in zip(*cols, strict=True):
        norm = [normalize_text(p) if p is not None else "" for p in parts]
        joined.append(None if not all(norm) else " ".join(norm))
    return joined


def candidate_pairs(
    table: pa.Table, rules: list[BlockRule], *, max_block: int = 500
) -> tuple[npt.NDArray[np.int64], dict[str, object]]:
    """The candidate pairs ``(i, j)`` with ``i < j``, sorted and unique, and the blocking stats.

    The stats: ``rules``, ``blocks`` (with two or more rows), ``skipped_blocks`` and
    ``skipped_records`` (rows in blocks larger than ``max_block``), ``pairs`` and ``by_rule``.
    """
    if not rules:
        raise ValueError("blocking needs at least one rule")
    if max_block < 2:
        raise ValueError("max_block must be at least 2")
    n = table.num_rows
    parts: list[npt.NDArray[np.int64]] = []
    blocks = skipped_blocks = skipped_records = 0
    by_rule: list[dict[str, object]] = []
    for rule in rules:
        groups: dict[str, list[int]] = {}
        for row, keys in enumerate(block_keys(_rule_values(table, rule), rule)):
            for key in keys:
                groups.setdefault(key, []).append(row)
        rule_parts: list[npt.NDArray[np.int64]] = []
        for rows in groups.values():
            m = len(rows)
            if m < 2:
                continue
            blocks += 1
            if m > max_block:
                skipped_blocks += 1
                skipped_records += m
                continue
            idx = np.asarray(rows, dtype=np.int64)
            a, b = np.triu_indices(m, 1)
            rule_parts.append(idx[a] * n + idx[b])
        rule_codes = np.unique(np.concatenate(rule_parts)) if rule_parts else np.empty(0, np.int64)
        by_rule.append({"rule": rule.label(), "pairs": int(len(rule_codes))})
        parts.append(rule_codes)
    codes = np.unique(np.concatenate(parts)) if parts else np.empty(0, np.int64)
    pairs = np.stack([codes // max(n, 1), codes % max(n, 1)], axis=1).astype(np.int64)
    pairs = pairs.reshape(-1, 2)
    stats: dict[str, object] = {
        "rules": len(rules),
        "blocks": blocks,
        "skipped_blocks": skipped_blocks,
        "skipped_records": skipped_records,
        "pairs": int(len(pairs)),
        "by_rule": by_rule,
    }
    return pairs, stats
