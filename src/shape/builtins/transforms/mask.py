"""``mask``: replace personal data in tables with synthetic values of the same format.

Columns are classified from their names and, where the name says nothing, from the value patterns
that Shape's profile engine finds (e-mail, phone, SSN, IPv4, IBAN, postal code). A masked column
keeps its type, its null positions and the format of its values (see ``_mask_values``). The same
original value becomes the same replacement wherever it appears (in a key and in the columns that
refer to it), so joins and group-bys still work; no replacement equals an original value (for
identifier types, any original value of the type).

Everything is deterministic for a seed.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from . import _mask_values as mv

SHAPE_API = "1.0"

_TEMPORAL_DTYPES = frozenset({"datetime", "date", "timestamp", "time", "duration", "timedelta"})
_BOOLEAN_DTYPES = frozenset({"boolean", "bool"})
_ISO_TEMPORAL = re.compile(
    r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d+)?)?)?(Z|[+-]\d{2}:?\d{2})?$"
)


class MaskError(ValueError):
    """The request cannot be carried out safely (an unknown type, an impossible replacement)."""


@dataclass
class MaskConfig:
    """Options of :func:`mask_tables`.

    ``pii_columns`` maps column names to one of the personal-data types (``_mask_values.TYPES``)
    and forces that column to be masked as that type; ``exclude_columns`` are never touched.
    """

    seed: int = 42
    pii_columns: Mapping[str, str] = field(default_factory=dict)
    exclude_columns: tuple[str, ...] = ()


@dataclass
class MaskResult:
    """Masked tables and what was done to them."""

    tables: dict[str, pa.Table]
    columns_masked: dict[str, list[str]]
    column_types: dict[str, dict[str, str]]  # {table: {masked column: type}}
    stats: dict[str, dict[str, int]]  # {table: {total_cols, masked_cols, rows}}

    def summary(self) -> str:
        lines = ["Masking result", "=" * 50]
        for table, s in self.stats.items():
            lines.append(
                f"  {table}: {s['masked_cols']}/{s['total_cols']} columns masked ({s['rows']} rows)"
            )
            cols = self.columns_masked.get(table, [])
            if cols:
                kinds = self.column_types[table]
                lines.append("    Masked: " + ", ".join(f"{c} ({kinds[c]})" for c in cols))
        return "\n".join(lines)


def _kind(arrow_type: pa.DataType, dtype: str | None) -> str:
    """How a column is masked: ``text`` / ``int`` / ``float`` / ``temporal`` / ``skip``."""
    if pa.types.is_string(arrow_type) or pa.types.is_large_string(arrow_type):
        if dtype in _TEMPORAL_DTYPES or dtype in _BOOLEAN_DTYPES:
            return "skip-text"
        if dtype == "integer":
            return "int-text"
        if dtype == "float":
            return "float-text"
        return "text"
    if pa.types.is_integer(arrow_type):
        return "int"
    if pa.types.is_floating(arrow_type):
        return "float"
    if pa.types.is_date(arrow_type) or pa.types.is_timestamp(arrow_type):
        return "temporal"
    return "skip"


def _looks_temporal(column: pa.ChunkedArray) -> bool:
    sample = [v for v in column.slice(0, 200).to_pylist() if v is not None]
    return bool(sample) and all(_ISO_TEMPORAL.match(str(v).strip()) for v in sample)


def _compatible(type_name: str, kind: str, column: pa.ChunkedArray) -> bool:
    if kind in ("skip", "float", "float-text"):
        return False
    if kind in ("int", "int-text"):
        return type_name in mv.NUMERIC_TYPES
    if kind == "temporal":
        return type_name == "date_of_birth"
    if kind == "skip-text":
        return type_name == "date_of_birth"
    if type_name == "date_of_birth":
        return True
    return not _looks_temporal(column)


def _detect(
    name: str,
    kind: str,
    column: pa.ChunkedArray,
    info: Mapping[str, Any],
    config: MaskConfig,
) -> str | None:
    if name in config.exclude_columns:
        return None
    forced = config.pii_columns.get(name)
    if forced is not None:
        if forced not in mv.TYPES:
            raise MaskError(f"unknown personal-data type {forced!r} for {name!r}")
        if kind in ("float", "float-text"):
            return forced
        if not _compatible(forced, kind, column):
            raise MaskError(f"column {name!r} cannot be masked as {forced!r}")
        return forced
    for candidate in (mv.type_from_name(name), mv.PATTERN_TYPES.get(str(info.get("pattern")))):
        if candidate is not None and _compatible(candidate, kind, column):
            return candidate
    return None


def _distinct_text(column: pa.ChunkedArray) -> list[str]:
    values = pc.unique(pc.drop_null(column.cast(pa.string())))
    return sorted(values.to_pylist())


def _seed_for(seed: int, label: str) -> np.random.Generator:
    return np.random.default_rng(np.random.SeedSequence([seed, zlib.crc32(label.encode())]))


def _mask_floats(column: pa.ChunkedArray, rng: np.random.Generator, as_text: bool) -> pa.Array:
    texts = column.to_pylist()
    present = [i for i, v in enumerate(texts) if v is not None]
    values = np.array([float(texts[i]) for i in present], dtype=np.float64)
    new = mv.float_replacements(values, rng)
    if not as_text:
        out: list[Any] = list(texts)
        for i, v in zip(present, new.tolist(), strict=True):
            out[i] = v
        return pa.array(out, type=column.type)
    decimals = max((len(str(texts[i]).partition(".")[2]) for i in present), default=0)
    out = list(texts)
    for i, v in zip(present, new.tolist(), strict=True):
        out[i] = f"{v:.{decimals}f}"
    return pa.array(out, type=column.type)


def _mask_temporal(column: pa.ChunkedArray, rng: np.random.Generator) -> pa.Array:
    ints = (
        column.cast(pa.int64()) if not pa.types.is_date32(column.type) else column.cast(pa.int32())
    )
    arr = ints.combine_chunks() if isinstance(ints, pa.ChunkedArray) else ints
    valid = pc.is_valid(arr).to_numpy(zero_copy_only=False)
    values = np.asarray(pc.fill_null(arr, 0).to_numpy(zero_copy_only=False), dtype=np.int64)
    if not valid.any():
        return column.combine_chunks()
    lo, hi = int(values[valid].min()), int(values[valid].max())
    if hi == lo:
        hi = lo + (365 if pa.types.is_date(column.type) else 365 * 86_400)
    new = values.copy()
    todo = valid.copy()
    for _ in range(40):
        new[todo] = rng.integers(lo, hi + 1, int(todo.sum()))
        todo = valid & (new == values)
        if not todo.any():
            break
    if todo.any():
        raise MaskError("cannot draw dates that differ from the originals")
    out = pa.array(new, type=pa.int64(), mask=~valid)
    if pa.types.is_date32(column.type):
        out = out.cast(pa.int32())
    return out.cast(column.type)


def _check_no_original(name: str, before: pa.ChunkedArray, after: pa.Array) -> None:
    """Defense in depth: no masked cell may still hold its original value."""
    same = pc.equal(before, after)
    if pc.any(same).as_py():
        raise MaskError(f"masking left original values in column {name!r}")


def mask_tables(
    tables: Mapping[str, pa.Table],
    config: MaskConfig | None = None,
    profile: Mapping[str, Any] | None = None,
) -> MaskResult:
    """Mask the personal data in ``tables``.

    ``profile`` is a dataset profile as a dict (``Profile.to_dict()`` of a profile of the same
    tables); without it the tables are profiled here. Pass the profile of the *typed* data when
    the tables hold text read without type inference, so that dates and numbers are recognised.
    """
    if isinstance(tables, pa.Table):
        raise TypeError("mask_tables needs a mapping of table name to table, not one table")
    cfg = config or MaskConfig()
    infos = _column_infos(tables, profile)

    kinds: dict[str, dict[str, str]] = {}
    types: dict[str, dict[str, str]] = {}
    for tname, table in tables.items():
        kinds[tname] = {}
        types[tname] = {}
        for cname in table.column_names:
            info = infos.get(tname, {}).get(cname, {})
            kind = _kind(table.schema.field(cname).type, info.get("dtype"))
            kinds[tname][cname] = kind
            found = _detect(cname, kind, table.column(cname), info, cfg)
            if found is not None:
                types[tname][cname] = found

    # A column that refers to a masked key is masked with it (it would otherwise point at nothing,
    # and still hold the key's original values).
    for tname, table in tables.items():
        for cname in table.column_names:
            info = infos.get(tname, {}).get(cname, {})
            ref = info.get("fk_ref_table")
            if (
                cname in types[tname]
                or cname in cfg.exclude_columns
                or not info.get("is_foreign_key")
            ):
                continue
            keys = infos.get(ref, {}).get("__primary_key__", []) if ref in tables else []
            if len(keys) == 1 and keys[0] in types.get(ref, {}):
                parent_type = types[ref][keys[0]]
                if _compatible(parent_type, kinds[tname][cname], table.column(cname)):
                    types[tname][cname] = parent_type

    # One mapping per type, from all the original values of that type in all the tables.
    maps: dict[str, tuple[pa.Array, pa.Array]] = {}
    for type_name in mv.TYPES:
        originals: set[str] = set()
        for tname, table in tables.items():
            for cname, t in types[tname].items():
                if t == type_name and kinds[tname][cname] in (
                    "text",
                    "int",
                    "int-text",
                    "skip-text",
                ):
                    originals.update(_distinct_text(table.column(cname)))
        if not originals:
            continue
        ordered = sorted(originals)
        try:
            new = mv.replacements(type_name, ordered, _seed_for(cfg.seed, type_name))
        except ValueError as exc:
            raise MaskError(str(exc)) from exc
        maps[type_name] = (pa.array(ordered, pa.string()), pa.array(new, pa.string()))

    masked: dict[str, pa.Table] = {}
    columns_masked: dict[str, list[str]] = {}
    column_types: dict[str, dict[str, str]] = {}
    stats: dict[str, dict[str, int]] = {}
    for tname, table in tables.items():
        out = table
        done: list[str] = []
        for cname in table.column_names:
            kind_name = types[tname].get(cname)
            if kind_name is None:
                continue
            kind = kinds[tname][cname]
            column = table.column(cname)
            label = f"{tname}.{cname}"
            if kind in ("float", "float-text"):
                new_col = _mask_floats(column, _seed_for(cfg.seed, label), kind == "float-text")
            elif kind == "temporal":
                new_col = _mask_temporal(column, _seed_for(cfg.seed, label))
            else:
                originals_arr, new_arr = maps[kind_name]
                idx = pc.index_in(column.cast(pa.string()), value_set=originals_arr)
                new_col = pc.take(new_arr, idx)
                try:
                    new_col = new_col.cast(column.type)
                except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as exc:
                    raise MaskError(
                        f"replacements do not fit the type of column {label!r}"
                    ) from exc
            _check_no_original(label, column, new_col)
            out = out.set_column(
                table.column_names.index(cname), table.schema.field(cname), new_col
            )
            done.append(cname)
        masked[tname] = out
        columns_masked[tname] = done
        column_types[tname] = {c: types[tname][c] for c in done}
        stats[tname] = {
            "total_cols": table.num_columns,
            "masked_cols": len(done),
            "rows": table.num_rows,
        }
    return MaskResult(masked, columns_masked, column_types, stats)


def _column_infos(
    tables: Mapping[str, pa.Table], profile: Mapping[str, Any] | None
) -> dict[str, dict[str, Any]]:
    """{table: {column: profile fields}}, with the table's key under ``__primary_key__``."""
    if profile is None:
        from shape.profile.reference import profile as run_profile

        profile = run_profile(dict(tables)).to_dict()
    infos: dict[str, dict[str, Any]] = {}
    for tname, tprof in (profile.get("tables") or {}).items():
        cols = {c: dict(v) for c, v in (tprof.get("columns") or {}).items()}
        cols["__primary_key__"] = list(tprof.get("primary_key") or [])  # type: ignore[assignment]
        infos[tname] = cols
    return infos


class Mask:
    """The ``mask`` transform: ``apply(tables, seed=42, exclude=(), pii={}, profile=None)``.

    ``mask`` takes the same options and returns the :class:`MaskResult` (which columns were masked,
    and as what), where ``apply`` returns only the tables."""

    name = "mask"

    def mask(self, tables: Mapping[str, pa.Table], **options: Any) -> MaskResult:
        known = {"seed", "exclude", "pii", "profile"}
        unknown = sorted(set(options) - known)
        if unknown:
            raise TypeError(f"unknown mask option(s): {', '.join(unknown)}")
        config = MaskConfig(
            seed=int(options.get("seed", 42)),
            pii_columns=dict(options.get("pii") or {}),
            exclude_columns=tuple(options.get("exclude") or ()),
        )
        return mask_tables(tables, config, options.get("profile"))

    def apply(self, tables: Mapping[str, pa.Table], **options: Any) -> dict[str, pa.Table]:
        return self.mask(tables, **options).tables
