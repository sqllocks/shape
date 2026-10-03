"""The mixed-type Gaussian-copula post-pass (W3-08): link numeric and categorical columns without
changing any column's values.

``joint.copula`` of a profile holds the latent correlation matrix of the numeric and the categorical
columns (``shape.profile.joint.multivariate.copula``): a category sits on the latent normal scale at
its mid-rank in a stored order (by frequency, ties by value). ``shape generate --from PROFILE
--mixed-copula`` writes it into the schema as ``generation.output.copula_mixed`` and the engine
applies :func:`apply_mixed_copula` after the numeric copula: per column, a latent normal from a
row-addressed stream (label ``copula_mixed:<column>``) is correlated through the Cholesky factor of
the matrix and the column's own generated values are reordered by its rank, so every column keeps
exactly its generated values (the marginal) and the result depends only on the table's contents and
the seed.

Left alone: key-like columns (``id``, ``pk``, names ending ``_id``, ``_pk``, ``_fk``), primary
keys, and every column whose value another column's generator reads (a conditional table, a
hierarchy, a derived or computed column, a lookup, a foreign key): :func:`ordered_columns`. A
column that this pass orders is taken out of the numeric copula's pairs (``without_pairs``): the
mixed matrix already holds the numeric correlations.

The persisted block declares ``format`` and ``version`` (:data:`FORMAT`, :data:`VERSION`).

Stable interface: :func:`apply_mixed_copula`, :func:`block_from_profile`, :func:`ordered_columns`.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.rng import RowStream

PROFILE_FORMAT = "shape.copula"  # the profile's `joint.copula` (shape.profile.joint.multivariate)
FORMAT = "shape.copula-mixed"
VERSION = 1
OUTPUT_KEY = "copula_mixed"

# strategies that draw a column from its own marginal: the ones whose values may be reordered
_INDEPENDENT = frozenset(
    {"weighted_enum", "choice", "distribution", "normal", "uniform", "empirical", "temporal"}
)
_REFERENCES = (("source_table", "source_column"), ("child_table", "child_column"))


def _looks_like_key(name: str) -> bool:
    n = name.lower()
    return n in ("id", "pk") or n.endswith(("_id", "_pk", "_fk"))


def block_from_profile(
    tables: Mapping[str, Mapping[str, Any] | None],
) -> dict[str, Any] | None:
    """The schema block for ``generation.output.copula_mixed``: each table's ``joint.copula``
    (``tables`` maps a table to its ``joint`` entry). None when no table has one."""
    out: dict[str, Any] = {}
    for name, joint in tables.items():
        cop = (joint or {}).get("copula")
        if not cop or cop.get("format") != PROFILE_FORMAT or int(cop.get("version", 0)) != 1:
            continue
        out[name] = {
            "columns": list(cop["columns"]),
            "numeric": list(cop["numeric"]),
            "categories": {k: list(v) for k, v in cop["categories"].items()},
            "correlation": [list(r) for r in cop["correlation"]],
        }
    if not out:
        return None
    return {"format": FORMAT, "version": VERSION, "tables": out}


def _check_block(block: Mapping[str, Any]) -> Mapping[str, Any]:
    if block.get("format") != FORMAT:
        raise ValueError(f"generation.output.{OUTPUT_KEY}: format must be {FORMAT!r}")
    version = block.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise ValueError(f"generation.output.{OUTPUT_KEY}: version must be an integer")
    if version > VERSION:
        raise ValueError(
            f"generation.output.{OUTPUT_KEY} has version {version}; this Shape reads {VERSION} "
            "and earlier: upgrade Shape"
        )
    tables = block.get("tables")
    if not isinstance(tables, Mapping):
        raise ValueError(f"generation.output.{OUTPUT_KEY}: 'tables' must be an object")
    return tables


def _followers(schema: Any, table: str) -> dict[str, list[str]]:
    """Each column of ``table`` that a ``conditional_table`` column draws from (directly or through
    other such columns): the columns drawn from it, in schema order."""
    columns = schema.tables[table].columns
    parent = {
        name: str(col.generator["source_column"])
        for name, col in columns.items()
        if col.strategy == "conditional_table" and col.generator.get("source_column") in columns
    }
    out: dict[str, list[str]] = {}
    for name in columns:
        up, seen = name, {name}
        while up in parent and parent[up] not in seen:
            up = parent[up]
            seen.add(up)
            out.setdefault(up, []).append(name)
    return out


def _held(schema: Any, table: str) -> set[str]:
    """The columns of ``table`` whose values something else reads or that must keep their rows."""
    tdef = schema.tables[table]
    held: set[str] = set(tdef.primary_key)
    names = list(tdef.columns)
    for cname, col in tdef.columns.items():
        strategy = col.strategy
        if strategy in _INDEPENDENT or strategy == "conditional_table":
            continue  # a conditional table's column moves with the column it is drawn from
        held.add(cname)
        text = str(sorted(col.generator.items(), key=lambda kv: kv[0]))
        for other in names:  # a column named in this generator is a source of it
            if other != cname and re.search(rf"(?<![\w]){re.escape(other)}(?![\w])", text):
                held.add(other)
    for other_name, other in schema.tables.items():  # a column another table reads
        for col in other.columns.values():
            gen = col.generator
            for t_key, c_key in _REFERENCES:
                if gen.get(t_key) == table and gen.get(c_key) in tdef.columns:
                    held.add(str(gen[c_key]))
            ref = gen.get("ref")
            if isinstance(ref, str) and other_name != table and ref.split(".")[0] == table:
                held.add(ref.split(".")[-1])
    return held


def ordered_groups(schema: Any, table: str) -> dict[str, list[str]]:
    """The columns the mixed copula reorders in ``table`` of ``schema`` (in the stored order), each
    with the columns that move with it: a column that ``conditional_table`` columns are drawn from
    is reordered together with them (the rows of the group move as one, so the conditional tables
    still hold). Empty when the schema has no block for the table or fewer than two columns are
    free."""
    block = schema.generation.output.get(OUTPUT_KEY)
    if not block:
        return {}
    spec = _check_block(block).get(table)
    if spec is None or table not in schema.tables:
        return {}
    held = _held(schema, table)
    have = schema.tables[table].columns
    follow = _followers(schema, table)
    followers = {f for fs in follow.values() for f in fs}
    free: dict[str, list[str]] = {}
    for c in spec["columns"]:
        if c not in have or c in held or c in followers or _looks_like_key(c):
            continue
        moved = follow.get(c, [])
        if any(m in held for m in moved):
            continue  # a column that moves with it is read by something else
        free[c] = moved
    return free if len(free) >= 2 else {}


def ordered_columns(schema: Any, table: str) -> list[str]:
    """The columns :func:`ordered_groups` reorders (without those that move with them)."""
    return list(ordered_groups(schema, table))


def without_pairs(pairs: Iterable[Sequence[Any]], ordered: Iterable[str]) -> list[list[Any]]:
    """The numeric copula's ``[a, b, r]`` pairs that touch no column the mixed copula orders."""
    skip = set(ordered)
    if not skip:
        return [list(p) for p in pairs]
    return [list(p) for p in pairs if p[0] not in skip and p[1] not in skip]


def _category_keys(values: pa.Array, categories: Sequence[str]) -> np.ndarray:
    """A sortable integer per value: its position in the stored order (a value the profile never
    saw goes last, in order of first appearance)."""
    encoded = pc.dictionary_encode(values)
    position = {name: i for i, name in enumerate(categories)}
    unknown = len(categories)
    pos = np.array(
        [position.get(str(v.as_py()), unknown) for v in encoded.dictionary], dtype=np.int64
    )
    idx = np.asarray(encoded.indices.to_numpy(zero_copy_only=False), dtype=np.int64)
    return pos[idx] * (len(pos) + 1) + idx


def _permutation(
    col: pa.Array, z: np.ndarray, categories: Sequence[str] | None
) -> np.ndarray | None:
    """The row permutation that reorders ``col``'s non-null values by the rank of ``z`` among the
    non-null rows (the sorted values go to the ranks); null rows stay. None when fewer than two
    rows are non-null."""
    keep = np.asarray(col.is_valid().to_numpy(zero_copy_only=False))
    present = np.flatnonzero(keep)
    if len(present) < 2:
        return None
    values = pc.take(col, pa.array(present))
    if pa.types.is_dictionary(values.type):
        values = values.dictionary_decode()
    ranks = np.argsort(np.argsort(z[present], kind="stable"), kind="stable")
    if categories is None:
        order = np.asarray(pc.sort_indices(values).to_numpy(zero_copy_only=False), dtype=np.int64)
    else:
        order = np.argsort(_category_keys(values, categories), kind="stable")
    perm = np.arange(len(col))
    perm[present] = present[order[ranks]]
    return perm


def apply_mixed_copula(table: pa.Table, schema: Any, seed: int, table_name: str) -> pa.Table:
    """``table`` with the columns of ``ordered_groups(schema, table_name)`` reordered by the mixed
    copula (each with the columns that move with it)."""
    groups = {
        c: [m for m in moved if m in table.column_names]
        for c, moved in ordered_groups(schema, table_name).items()
        if c in table.column_names
    }
    ordered = list(groups)
    if len(ordered) < 2:
        return table
    spec = _check_block(schema.generation.output[OUTPUT_KEY])[table_name]
    names = list(spec["columns"])
    full = np.array(spec["correlation"], dtype=np.float64)
    index = [names.index(c) for c in ordered]
    sub = full[np.ix_(index, index)]
    vals, vecs = np.linalg.eigh((sub + sub.T) / 2.0)
    fixed = (vecs * np.clip(vals, 1e-6, None)) @ vecs.T
    scale = 1.0 / np.sqrt(np.diag(fixed))
    fixed = fixed * scale[:, None] * scale[None, :]
    try:
        chol = np.linalg.cholesky(fixed)
    except np.linalg.LinAlgError:
        return table
    n = table.num_rows
    z = np.stack(
        [RowStream(seed, table_name, "*", f"copula_mixed:{c}").normal(0, n) for c in ordered],
        axis=1,
    )
    z = z @ chol.T
    categories = spec["categories"]
    out = table
    for i, c in enumerate(ordered):
        perm = _permutation(table[c].combine_chunks(), z[:, i], categories.get(c))
        if perm is None:
            continue
        take = pa.array(perm)
        for name in (c, *groups[c]):
            out = out.set_column(out.column_names.index(name), name, pc.take(table[name], take))
    return out
