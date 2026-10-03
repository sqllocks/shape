"""The skew rehearsal: generate a schema at scale with the profile's key skew (W5-10,
``docs/SCALE.md``).

A scale test with uniform keys misses the hot keys production has. ``rehearse`` reads the measured
concentration of each key column from the profile (the share of the rows held by the most frequent
keys, from the profile's top-value frequencies), generates the schema at the given scale with that
concentration on the matching ``foreign_key`` columns (``fan_out``, drawn through
:func:`shape.generation.fanout.concentration_weights`) and compares, per column, the profile's top
share with the generated one (:func:`shape.generation.fanout.top_share_of`) against a tolerance.

Concentration of a column: with ``c`` distinct keys in the profile and the ``m`` most frequent
listed with their frequencies, ``k = min(m, max(1, round(0.2 * c)))`` keys form the top, so the top
fraction is ``k / c`` (0.2 when the profile lists enough keys) and the top share is the sum of the
``k`` largest frequencies. ``k`` keys out of ``c`` hold that share in the profile; the generated
column must make the same fraction of its parents hold the same share. Both are shares of the
non-null rows, so they do not depend on the scale.
"""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation.fanout import top_share_of

FORMAT = "shape-skew-report"
VERSION = 1
DEFAULT_TOLERANCE = 0.02
TOP_FRACTION = 0.2
REPORT = "skew_report.json"
_BYPASS = ("constrained_by", "sample_rate")


class SkewInputError(ValueError):
    """The profile has no frequency data for a requested column, or the request names a column
    that cannot be skewed (exit 2)."""


@dataclass
class Concentration:
    """The measured concentration of one key column of the profile."""

    cardinality: int
    listed: int
    top_fraction: float
    top_share: float


def measure(column: dict[str, Any]) -> Concentration | None:
    """The concentration of a profile column document, or ``None`` when it has no frequency data
    (no top values, or fewer than two distinct keys)."""
    counts = column.get("value_counts_ext") or {}
    card = column.get("cardinality")
    if not counts or not isinstance(card, int) or card < 2:
        return None
    freqs = sorted((float(v) for v in counts.values()), reverse=True)
    k = max(1, min(len(freqs), round(TOP_FRACTION * card)))
    if k >= card:
        return None
    fraction = k / card
    share = min(max(round(sum(freqs[:k]), 6), fraction), 0.999999)
    return Concentration(card, len(freqs), fraction, share)


def _profile_columns(profile: Any) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for table, doc in profile.tables.items():
        for name, col in (doc.get("columns") or {}).items():
            out[f"{table}.{name}"] = col
    return out


def _fan_out(c: Concentration) -> dict[str, Any]:
    """The ``fan_out`` spec of the generated column: ``c``'s fraction and share, power shape."""
    return {
        "top_fraction": c.top_fraction,
        "top_share": c.top_share,
        "shape": "power",
        "shuffle": True,
    }


@dataclass
class ColumnResult:
    column: str
    references: str
    parents: int
    rows: int
    top_fraction: float
    profile_top_share: float
    generated_top_share: float
    tolerance: float
    shape: str

    @property
    def difference(self) -> float:
        return abs(self.generated_top_share - self.profile_top_share)

    @property
    def within(self) -> bool:
        return self.difference <= self.tolerance

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "references": self.references,
            "parents": self.parents,
            "rows": self.rows,
            "top_fraction": round(self.top_fraction, 6),
            "shape": self.shape,
            "profile_top_share": round(self.profile_top_share, 6),
            "generated_top_share": round(self.generated_top_share, 6),
            "difference": round(self.difference, 6),
            "within_tolerance": self.within,
        }


@dataclass
class Rehearsal:
    scale: str
    seed: int
    tolerance: float
    columns: list[ColumnResult] = field(default_factory=list)
    skipped: list[dict[str, str]] = field(default_factory=list)
    files: list[str] = field(default_factory=list)

    @property
    def within(self) -> bool:
        return all(c.within for c in self.columns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "version": VERSION,
            "scale": self.scale,
            "seed": self.seed,
            "tolerance": self.tolerance,
            "within_tolerance": self.within,
            "columns": [c.to_dict() for c in self.columns],
            "skipped": self.skipped,
            "files": self.files,
        }


def _fk_columns(schema: Any) -> dict[str, Any]:
    return {
        f"{t}.{c.name}": c
        for t, tdef in schema.tables.items()
        for c in tdef.columns.values()
        if c.is_foreign_key
    }


def plan(
    profile: Any, schema: Any, requested: list[str] | None
) -> tuple[dict[str, Concentration], list[dict[str, str]]]:
    """The columns to skew with their concentrations, and the default candidates left out (each
    with its reason). A column named in ``requested`` that cannot be skewed raises
    :class:`SkewInputError`."""
    stats = _profile_columns(profile)
    fks = _fk_columns(schema)
    chosen: dict[str, Concentration] = {}
    skipped: list[dict[str, str]] = []
    if requested is not None:
        for name in requested:
            if name not in fks:
                if "." not in name or name.split(".", 1)[0] not in schema.tables:
                    raise SkewInputError(
                        f"{name!r} is not a column of the schema (use TABLE.COLUMN)"
                    )
                raise SkewInputError(f"{name} is not a foreign-key column of the schema")
            bypass = [k for k in _BYPASS if fks[name].generator.get(k) is not None]
            if bypass:
                raise SkewInputError(
                    f"{name} uses {bypass[0]}, which draws its keys without the fan-out"
                )
            found = measure(stats[name]) if name in stats else None
            if found is None:
                raise SkewInputError(f"the profile has no frequency data for {name}")
            chosen[name] = found
        return chosen, skipped
    for name, col in sorted(fks.items()):
        bypass = [k for k in _BYPASS if col.generator.get(k) is not None]
        if bypass:
            skipped.append({"column": name, "reason": f"uses {bypass[0]}"})
            continue
        found = measure(stats[name]) if name in stats else None
        if found is None:
            skipped.append({"column": name, "reason": "the profile has no frequency data"})
            continue
        chosen[name] = found
    return chosen, skipped


def parent_indices(child: pa.ChunkedArray, parent: pa.ChunkedArray) -> tuple[Any, int]:
    """Each non-null child value as the rank of its parent among the distinct parent keys, and the
    number of parents."""
    keys = pc.unique(parent.drop_null() if hasattr(parent, "drop_null") else parent)
    keys = keys.combine_chunks() if isinstance(keys, pa.ChunkedArray) else keys
    index = pc.index_in(child.drop_null(), value_set=keys).drop_null()
    return np.asarray(index.to_numpy(zero_copy_only=False), dtype=np.int64), len(keys)


def generated_share(
    child: pa.ChunkedArray, parent: pa.ChunkedArray, fraction: float
) -> tuple[float, int]:
    """The share of the child rows held by the ``fraction`` of parents with the most children, as
    :func:`shape.generation.fanout.top_share_of` measures it, and the parent count. The fraction is
    the one :class:`shape.generation.fanout.FanOut` rounds to a whole number of parents."""
    indices, n = parent_indices(child, parent)
    if n < 2 or len(indices) == 0:
        return 0.0, n
    k = max(1, min(n - 1, round(n * fraction)))
    # top_share_of takes ceil(n * fraction) parents; (k - 0.5) / n makes that exactly k.
    return top_share_of(indices, n, (k - 0.5) / n), n


def rehearse(
    profile: Any,
    schema: Any,
    scale: str,
    *,
    seed: int | None = None,
    columns: list[str] | None = None,
    tolerance: float = DEFAULT_TOLERANCE,
    out_dir: str | os.PathLike[str] | None = None,
) -> Rehearsal:
    """Generate ``schema`` at ``scale`` with the profile's skew and compare it. With ``out_dir``
    the tables are written there as Parquet files, with ``skew_report.json``."""
    from shape.generation.engine import Engine

    if not 0 < tolerance < 1:
        raise SkewInputError("--tolerance must be between 0 and 1")
    chosen, skipped = plan(profile, schema, columns)
    if not chosen:
        raise SkewInputError(
            "no foreign-key column of the schema has frequency data in the profile; nothing to "
            "rehearse"
        )
    skewed = copy.deepcopy(schema)
    specs = {name: _fan_out(c) for name, c in chosen.items()}
    for name, spec in specs.items():
        table, column = name.split(".", 1)
        skewed.tables[table].columns[column].generator["fan_out"] = spec
    engine = Engine(skewed, scale=scale, seed=seed)
    result = engine.generate()
    rehearsal = Rehearsal(scale=scale, seed=engine.seed, tolerance=tolerance, skipped=skipped)
    for name, c in sorted(chosen.items()):
        table, column = name.split(".", 1)
        col = skewed.tables[table].columns[column]
        ref_table, ref_column = str(col.fk_ref_table), str(col.fk_ref_column)
        share, parents = generated_share(
            result.tables[table].column(column),
            result.tables[ref_table].column(ref_column),
            c.top_fraction,
        )
        rehearsal.columns.append(
            ColumnResult(
                column=name,
                references=f"{ref_table}.{ref_column}",
                parents=parents,
                rows=result.tables[table].num_rows,
                top_fraction=c.top_fraction,
                profile_top_share=c.top_share,
                generated_top_share=share,
                tolerance=tolerance,
                shape=specs[name]["shape"],
            )
        )
    if out_dir is not None:
        from shape.generation.output import write_result

        out = Path(out_dir)
        paths = write_result(result, "parquet", out)
        rehearsal.files = sorted(p.name for p in paths)
        report = out / REPORT
        report.write_text(
            json.dumps(rehearsal.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    return rehearsal
