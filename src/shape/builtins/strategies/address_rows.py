"""The ``address`` strategy's engine: coherent addresses for whole chunks, row addressed.

A *reference* is a table of places (city, state, postal code, latitude, longitude, ...): a
registered dataset (``{"dataset": "us_zip_locations"}``, the default, shipped by the
``sqllocks-shape-domains`` package), rows given inline (dicts, ``AddressReference``, ``Location``,
or what ``load_geonames_postal`` returns), compiled once per engine. A *scope* picks the places
(`{"state": "WA"}`, `{"postal_code": "98101"}`, `{"city": "Seattle", "state": "WA"}`, a ZIP string,
``"Seattle, WA"``, ``"WA"``), with optional weights. Every row draws one place and a street number
from streams keyed by the row (never the chunk), so the row is the same for any chunking. All
address columns of a table that share a ``group`` (default: one group) draw the same place for
the same row: a ``city`` column, a ``state`` column and a ``postal_code`` column agree.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.reference import Dataset, DatasetNotFoundError, load_dataset
from shape.generation.rng import RowStream
from shape.generation.strategy_kit import StrategyError, where
from shape.location import Location, LocationScope, scope_from_specs
from shape.plugins.api.v1 import GenerationContext

from .address import AddressReference
from .providers import pool

DEFAULT_REFERENCE = "us_zip_locations"
MODES = ("geographic", "street_synthetic", "reference", "exact_reference")
FIELDS = (
    "address_line_1",
    "city",
    "county",
    "state",
    "postal_code",
    "country",
    "latitude",
    "longitude",
    "timezone",
    "mode",
    "reference_id",
)
_FIELD_ALIASES = {
    "street": "address_line_1",
    "address": "address_line_1",
    "zip": "postal_code",
    "lat": "latitude",
    "lng": "longitude",
    "lon": "longitude",
}
_JITTER = 0.002  # degrees: how far a synthetic address sits from its reference point
_SYNTHETIC_STREET = "Synthetic Way"

# Names a reference dataset or dict row may give each part (the first found is used).
_COLUMN_NAMES: dict[str, tuple[str, ...]] = {
    "city": ("city", "place", "place_name"),
    "county": ("county",),
    "state": ("state", "state_code", "admin1_code"),
    "postal_code": ("postal_code", "zip", "postcode", "zip_code"),
    "country": ("country", "country_code"),
    "latitude": ("latitude", "lat"),
    "longitude": ("longitude", "lng", "lon", "long"),
    "street": ("street", "address_line_1", "address"),
    "timezone": ("timezone", "tz"),
    "reference_id": ("canonical_location_id", "reference_id", "canonical_id", "id"),
}


@dataclass(frozen=True)
class Compiled:
    """A reference and a scope worked out for drawing: the columns of the places, and for every
    scope entry the indices of its places."""

    columns: dict[str, pa.Array]  # string columns (null where the reference has none)
    latitude: npt.NDArray[np.float64]
    longitude: npt.NDArray[np.float64]
    suffix: pa.Array | None  # the street's words after the number, when the reference has streets
    members: npt.NDArray[np.int64]  # place indices of every scope entry, one after the other
    offsets: npt.NDArray[np.int64]  # where each scope entry starts in ``members``
    sizes: npt.NDArray[np.int64]
    cumulative: npt.NDArray[np.float64]  # cumulative scope weights, ending at 1
    has_streets: bool


# ---- the reference ------------------------------------------------------------------------


def _plain_rows(reference: Any) -> list[Any]:
    """The rows of an inline reference. The output of ``load_geonames_postal`` (locations and
    its provenance) is taken as it is."""
    if (
        isinstance(reference, (list, tuple))
        and len(reference) == 2
        and isinstance(reference[0], (list, tuple))
    ):  # (locations, provenance); a schema document holds the pair as a list
        return list(reference[0])
    if not isinstance(reference, Sequence) or isinstance(reference, (str, bytes)):
        raise TypeError("the address reference is a list of rows or {'dataset': name}")
    return list(reference)


def _row_dict(row: Any) -> Mapping[str, Any]:
    if isinstance(row, Mapping):
        return row
    if isinstance(row, Location):
        return {
            "city": row.city,
            "county": row.county,
            "state": row.state,
            "postal_code": row.postal_code,
            "country": row.country,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "timezone": row.timezone,
            "reference_id": row.canonical_id,
        }
    if isinstance(row, AddressReference):
        return {
            "street": row.street,
            "city": row.city,
            "county": row.county,
            "state": row.state,
            "postal_code": row.postal_code,
            "country": row.country,
            "latitude": row.latitude,
            "longitude": row.longitude,
            "timezone": row.timezone,
            "reference_id": row.canonical_location_id,
        }
    if dataclasses.is_dataclass(row) and not isinstance(row, type):
        return dataclasses.asdict(row)
    raise TypeError(
        f"an address reference row is a dict, AddressReference or Location, not {row!r}"
    )


def _from_rows(rows: list[Any]) -> dict[str, pa.Array]:
    dicts = [_row_dict(r) for r in rows]

    def first(row: Mapping[str, Any], names: tuple[str, ...]) -> Any:
        for name in names:
            if row.get(name) is not None:
                return row[name]
        return None

    out: dict[str, pa.Array] = {}
    for part, names in _COLUMN_NAMES.items():
        values = [first(d, names) for d in dicts]
        if part in ("latitude", "longitude"):
            out[part] = pa.array(values, type=pa.float64())
        else:
            out[part] = pa.array([None if v is None else str(v) for v in values], pa.string())
    return out


def _from_dataset(ds: Dataset) -> dict[str, pa.Array]:
    lowered = {f.casefold(): f for f in ds.fields}
    out: dict[str, pa.Array] = {}
    n = len(ds)
    for part, names in _COLUMN_NAMES.items():
        found = next((lowered[x] for x in names if x in lowered), None)
        if found is None:
            out[part] = (
                pa.nulls(n, pa.float64())
                if part in ("latitude", "longitude")
                else pa.nulls(n, pa.string())
            )
        elif part in ("latitude", "longitude"):
            out[part] = ds.column(found).cast(pa.float64())
        else:
            out[part] = ds.column(found).cast(pa.string())
    for needed in ("city", "state", "postal_code", "latitude", "longitude"):
        if out[needed].null_count == n:
            raise StrategyError(f"the address reference {ds.name!r} has no {needed} column")
    if out["country"].null_count == n:
        out["country"] = pa.array(["US"] * n, pa.string())  # a dataset of US places
    if out["reference_id"].null_count == n:
        out["reference_id"] = pc.binary_join_element_wise(
            pa.array([f"{ds.name}:"] * n, pa.string()), out["postal_code"], ""
        )
    return out


def _default_dataset(name: str, ctx: GenerationContext) -> Dataset:
    try:
        return load_dataset(name)
    except DatasetNotFoundError:
        if name != DEFAULT_REFERENCE:
            raise StrategyError(
                f"the address reference dataset {name!r} is not registered ({where(ctx)}); "
                "register it with shape.generation.reference.register_dataset"
            ) from None
    # the default reference ships with the domain package: loading its domain registers it
    try:
        from shape.generation.domains import DomainNotFoundError, load_domain

        load_domain("retail")
        return load_dataset(name)
    except (DomainNotFoundError, DatasetNotFoundError):
        raise StrategyError(
            f"the address strategy has no reference places ({where(ctx)}): give "
            "'reference' rows or {'dataset': name}, or install the data: "
            "pip install sqllocks-shape-domains (it ships the dataset 'us_zip_locations')"
        ) from None


def _reference_columns(spec: Mapping[str, Any], ctx: GenerationContext) -> dict[str, pa.Array]:
    reference = spec.get("reference")
    if reference is None:
        return _from_dataset(_default_dataset(DEFAULT_REFERENCE, ctx))
    if isinstance(reference, Mapping):
        name = reference.get("dataset")
        if not name:
            raise StrategyError(
                f"address 'reference' is a list of rows or {{'dataset': name}} ({where(ctx)})"
            )
        return _from_dataset(_default_dataset(str(name), ctx))
    try:
        rows = _plain_rows(reference)
    except TypeError as exc:
        raise StrategyError(f"{exc} ({where(ctx)})") from exc
    if not rows:
        raise StrategyError(f"the address reference has no rows ({where(ctx)})")
    try:
        return _from_rows(rows)
    except TypeError as exc:
        raise StrategyError(f"{exc} ({where(ctx)})") from exc


# ---- the scope ----------------------------------------------------------------------------


def _equal(column: pa.Array, value: str) -> npt.NDArray[np.bool_]:
    hit = pc.equal(pc.utf8_lower(column), pa.scalar(value.casefold()))
    return np.asarray(pc.fill_null(hit, False).to_numpy(zero_copy_only=False), dtype=bool)


def _mask(columns: Mapping[str, pa.Array], loc: Location) -> npt.NDArray[np.bool_]:
    n = len(columns["city"])
    out = np.ones(n, dtype=bool)
    for attr in ("country", "state", "county", "city", "postal_code"):
        wanted = getattr(loc, attr)
        if wanted:
            out &= _equal(columns[attr], str(wanted))
    return out


def _default_locations(columns: Mapping[str, pa.Array]) -> list[Location]:
    """Every (country, state) of the reference, each with the same weight."""
    pairs = (
        pa.table(
            {"c": pc.fill_null(columns["country"], ""), "s": pc.fill_null(columns["state"], "")}
        )
        .group_by(["c", "s"])
        .aggregate([])
    )
    found = sorted(zip(pairs["c"].to_pylist(), pairs["s"].to_pylist(), strict=True))
    return [Location(country=c or "US", state=s) for c, s in found if s]


def _scope(
    spec: Mapping[str, Any], columns: Mapping[str, pa.Array], ctx: GenerationContext
) -> tuple[list[npt.NDArray[np.int64]], npt.NDArray[np.float64]]:
    """The place indices of every scope entry and the entries' weights."""
    raw = spec.get("scope")
    try:
        if raw is None or raw == []:
            locations: list[Any] = _default_locations(columns)
        elif isinstance(raw, (str, Mapping, Location)):
            locations = [raw]
        else:
            locations = list(raw)
        scope = scope_from_specs(  # type: ignore[no-untyped-call]
            locations, spec.get("weights"), spec.get("exclude", ())
        )
    except (ValueError, TypeError, KeyError) as exc:
        raise StrategyError(f"address scope: {exc} ({where(ctx)})") from exc
    return _eligible(scope, columns, ctx), np.asarray(scope.normalized_weights, dtype=np.float64)


def _eligible(
    scope: LocationScope, columns: Mapping[str, pa.Array], ctx: GenerationContext
) -> list[npt.NDArray[np.int64]]:
    excluded = [_mask(columns, x) for x in scope.exclude]
    groups: list[npt.NDArray[np.int64]] = []
    for wanted in scope.include:
        keep = _mask(columns, wanted.location)
        for hit in excluded:
            keep &= ~hit
        members = np.flatnonzero(keep).astype(np.int64)
        if not len(members):
            raise StrategyError(
                f"the address reference has no place for {wanted.location} ({where(ctx)})"
            )
        groups.append(members)
    return groups


def compile_reference(spec: Mapping[str, Any], ctx: GenerationContext) -> Compiled:
    """Work out the reference and scope of a spec (done once per engine and column)."""
    mode = str(spec.get("mode", "street_synthetic"))
    if mode not in MODES:
        raise StrategyError(
            f"unsupported address mode {mode!r}; the modes are {', '.join(MODES)} ({where(ctx)})"
        )
    columns = _reference_columns(spec, ctx)
    groups, weights = _scope(spec, columns, ctx)
    streets = columns["street"]
    has_streets = streets.null_count < len(streets)
    if mode in ("reference", "exact_reference") and not has_streets:
        raise StrategyError(f"address mode {mode!r} needs streets in the reference ({where(ctx)})")
    suffix = None
    if has_streets:
        # the words after the house number: `100 N High St` -> `N High St`
        suffix = pc.fill_null(pc.replace_substring_regex(streets, r"^[^ ]* ", ""), "")
    sizes = np.asarray([len(g) for g in groups], dtype=np.int64)
    return Compiled(
        columns=columns,
        latitude=np.asarray(columns["latitude"].fill_null(float("nan")).to_numpy()),
        longitude=np.asarray(columns["longitude"].fill_null(float("nan")).to_numpy()),
        suffix=suffix,
        members=np.concatenate(groups),
        offsets=np.concatenate([[0], np.cumsum(sizes)[:-1]]).astype(np.int64),
        sizes=sizes,
        cumulative=np.cumsum(weights / weights.sum()),
        has_streets=has_streets,
    )


# ---- drawing --------------------------------------------------------------------------------


def _streams(ctx: GenerationContext, spec: Mapping[str, Any]) -> tuple[str, str]:
    """The stream's column name: the group, shared by the address columns of the table."""
    return ctx.table, str(spec.get("group", "address"))


def draw(
    spec: Mapping[str, Any], compiled: Compiled, ctx: GenerationContext
) -> dict[str, pa.Array]:
    """Every address field for rows ``ctx.row_start ..``: one place and one street number per
    row, drawn from streams keyed by (seed, table, group, row)."""
    table, group = _streams(ctx, spec)
    start, n = ctx.row_start, ctx.n_rows

    def uniform(label: str) -> npt.NDArray[np.float64]:
        return RowStream(ctx.seed, table, group, label).uniform(start, n)

    mode = str(spec.get("mode", "street_synthetic"))
    entry = np.minimum(
        np.searchsorted(compiled.cumulative, uniform("entry"), side="right"),
        len(compiled.sizes) - 1,
    )
    within = np.minimum(
        (uniform("place") * compiled.sizes[entry]).astype(np.int64), compiled.sizes[entry] - 1
    )
    place = compiled.members[compiled.offsets[entry] + within]
    take = arrow_array(place, type=pa.int64())
    out: dict[str, pa.Array] = {
        part: pc.take(compiled.columns[part], take)
        for part in ("city", "county", "state", "postal_code", "country", "timezone")
    }
    out["reference_id"] = pc.take(compiled.columns["reference_id"], take)
    lat, lon = compiled.latitude[place].copy(), compiled.longitude[place].copy()
    if mode not in ("reference", "exact_reference"):
        lat += (uniform("lat") * 2 - 1) * _JITTER
        lon += (uniform("lon") * 2 - 1) * _JITTER
    out["latitude"], out["longitude"] = arrow_array(lat), arrow_array(lon)
    out["address_line_1"] = _street(mode, compiled, place, uniform, n)
    out["mode"] = pa.array([mode] * n, pa.string())
    return out


def _street(
    mode: str,
    compiled: Compiled,
    place: npt.NDArray[np.int64],
    uniform: Any,
    n: int,
) -> pa.Array:
    if mode in ("reference", "exact_reference"):
        return pc.take(compiled.columns["street"], arrow_array(place, type=pa.int64()))
    number = arrow_array(1 + np.minimum((uniform("number") * 9999).astype(np.int64), 9998))
    if mode == "geographic":
        return kernel_ops.template_strings(["", f" {_SYNTHETIC_STREET}"], [(0, 0)], [number], n)
    if compiled.suffix is not None:
        words = pc.take(compiled.suffix, arrow_array(place, type=pa.int64()))
        return kernel_ops.template_strings(["", " ", ""], [(0, 0), (1, 0)], [number, words], n)
    # a reference with no streets: a name and a suffix from Shape's own street pools
    names, suffixes = pool("street_names"), pool("street_suffixes")
    name = kernel_ops.pool_take(
        names, np.minimum((uniform("street") * len(names)).astype(np.int64), len(names) - 1)
    )
    kind = kernel_ops.pool_take(
        suffixes,
        np.minimum((uniform("suffix") * len(suffixes)).astype(np.int64), len(suffixes) - 1),
    )
    return kernel_ops.template_strings(
        ["", " ", " ", ""], [(0, 0), (1, 0), (2, 0)], [number, name, kind], n
    )


def field_name(spec: Mapping[str, Any]) -> str | None:
    field = spec.get("field")
    if field is None:
        return None
    name = _FIELD_ALIASES.get(str(field), str(field))
    if name not in FIELDS:
        raise ValueError(f"unknown address field {field!r}; the fields are {', '.join(FIELDS)}")
    return name


def compiled_for(spec: Mapping[str, Any], ctx: GenerationContext) -> Compiled:
    """The compiled reference of this column: once per engine (the same for every chunk and
    thread), else once per call."""
    engine = getattr(ctx, "engine", None)
    if engine is None:
        return compile_reference(spec, ctx)
    result: Compiled = engine.cached(
        ("address", ctx.table, ctx.column), lambda: compile_reference(spec, ctx)
    )
    return result


def generate(spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
    field = field_name(spec)
    out = draw(spec, compiled_for(spec, ctx), ctx)
    if field is not None:
        return out[field]
    return pa.StructArray.from_arrays([out[f] for f in FIELDS], names=list(FIELDS))
