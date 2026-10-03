"""Built-in strategy ``locale``: basic locale packs (W4-03, issue #74).

A locale is a country: ``US``, ``CA``, ``GB``, ``DE``, ``FR``, ``IN`` or ``AU`` (also written
``fr_FR``, ``fr-FR`` or ``fr``). ``provider`` is one of ``city``, ``region``, ``postcode``,
``phone_number``, ``first_name``, ``last_name`` or ``name``. The data is in ``locales/`` (see its
``MANIFEST.json``; every source and licence is quoted in ``THIRD_PARTY_NOTICES.md``):

* places and postal codes: GeoNames (CC BY 4.0), one place per postal code. ``city``, ``region``
  and ``postcode`` columns of a table with the same ``group`` (default ``address``) draw the same
  place for the same row, so they agree. GeoNames lists only the outward part of a British
  postcode and the first three characters of a Canadian one; the rest is drawn at random;
* phone numbers only in ranges a country reserves for fiction: ``US`` and ``CA`` the 555-0100 to
  555-0199 lines, ``FR`` the audiovisual roots of ARCEP's national numbering plan. A country
  without a shipped range raises a ``StrategyError``;
* first names where an openly licensed list is shipped (``FR``: INSEE; ``US``: Shape's own pools).
  Anywhere else the provider says that no openly licensed list is shipped;
* no national identifier (social security, insurance, tax or passport number, ...) is generated
  for any country.

Every draw is row addressed: the value of row ``r`` depends on the seed, the table, the column or
group, and ``r``, never on the chunk.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.domains import DomainNotFoundError, load_domain
from shape.generation.reference import DatasetNotFoundError, load_dataset
from shape.generation.rng import RowStream
from shape.generation.strategy_kit import StrategyError, where
from shape.plugins.api.v1 import GenerationContext

from .providers import _truncate
from .providers import pool as _builtin_pool

SHAPE_API = "1.0"

DATA_DIR = Path(__file__).resolve().parent / "locales"
COUNTRIES = ("US", "CA", "GB", "DE", "FR", "IN", "AU")
PROVIDERS = ("city", "region", "postcode", "phone_number", "first_name", "last_name", "name")
# Providers that would make a national identifier: always refused (issue #74).
NATIONAL_IDENTIFIERS = frozenset(
    {
        "ssn", "sin", "nino", "nir", "insee", "aadhaar", "pan", "tfn", "medicare",
        "steuer_id", "steuerid", "national_id", "passport", "driver_license", "tax_id",
        "social_security", "ni_number",
    }
)  # fmt: skip
# Phone ranges a country reserves for fiction: the country code and the national patterns.
FRENCH_ROOTS = ("01 99 00", "02 61 91", "03 53 01", "04 65 71", "05 36 49", "06 39 98")
LETTERS_GB = "ABDEFGHJLNPQRSTUWXYZ"  # the letters an inward code uses
LETTERS_CA = "ABCEGHJKLMNPRSTVWXYZ"  # the letters after the first digit of a postal code


def normalize(locale: Any) -> str | None:
    """The country code of ``fr_FR``, ``fr-FR``, ``FR`` or ``fr``, or ``None`` if it is not one."""
    text = str(locale or "").strip().replace("-", "_")
    code = text.split("_")[-1].upper()
    return code if code in COUNTRIES and len(text) <= 5 else None


@cache
def _manifest() -> dict[str, Any]:
    manifest: dict[str, Any] = json.loads((DATA_DIR / "MANIFEST.json").read_text("utf-8"))
    return manifest


@cache
def _file_places(country: str) -> dict[str, list[Any]]:
    out: dict[str, list[Any]] = {
        k: [] for k in ("city", "region", "postal_code", "latitude", "longitude")
    }
    with (DATA_DIR / f"{country.lower()}_places.tsv").open(encoding="utf-8", newline="") as fh:
        for city, region, code, lat, lng in csv.reader(fh, delimiter="\t"):
            out["city"].append(city)
            out["region"].append(region)
            out["postal_code"].append(code)
            out["latitude"].append(float(lat))
            out["longitude"].append(float(lng))
    return out


@cache
def _us_places() -> dict[str, list[Any]]:
    try:
        ds = load_dataset("us_zip_locations")
    except DatasetNotFoundError:
        try:
            load_domain("retail")
            ds = load_dataset("us_zip_locations")
        except (DomainNotFoundError, DatasetNotFoundError):
            raise StrategyError(
                "the US places of the locale strategy ship with sqllocations-shape-domains: "
                "pip install sqllocations-shape-domains"
            ) from None
    cols = {f.casefold(): ds.column(f).to_pylist() for f in ds.fields}
    return {
        "city": cols["city"],
        "region": cols["state"],
        "postal_code": [str(z) for z in cols.get("postal_code", cols.get("zip", []))],
        "latitude": cols.get("latitude", cols.get("lat", [])),
        "longitude": cols.get("longitude", cols.get("lng", cols.get("lon", []))),
    }


def places(country: str) -> dict[str, list[Any]]:
    """The places of ``country``: ``city``, ``region``, ``postal_code``, ``latitude``, ``longitude``
    and ``country`` (one per place)."""
    raw = _us_places() if country == "US" else _file_places(country)
    return {**raw, "country": [country] * len(raw["city"])}


def pool(country: str, name: str) -> list[str]:
    """The name list ``name`` (``first_names``) of ``country``, one entry per name."""
    path = DATA_DIR / f"{country.lower()}_{name}.txt"
    return path.read_text("utf-8").splitlines()


@cache
def _arrow_places(country: str) -> tuple[pa.Array, pa.Array, pa.Array]:
    data = places(country)
    return (
        arrow_array(data["city"], type=pa.string()),
        arrow_array(data["region"], type=pa.string()),
        arrow_array(data["postal_code"], type=pa.string()),
    )


@cache
def _arrow_pool(country: str, name: str) -> pa.Array:
    return arrow_array(pool(country, name), type=pa.string())


def _uniform(ctx: GenerationContext, owner: str, label: str) -> npt.NDArray[np.float64]:
    return RowStream(ctx.seed, ctx.table, owner, label).uniform(ctx.row_start, ctx.n_rows)


def _pick(ctx: GenerationContext, owner: str, label: str, size: int) -> npt.NDArray[np.int64]:
    return np.minimum((_uniform(ctx, owner, label) * size).astype(np.int64), size - 1)


def _ints(ctx: GenerationContext, owner: str, label: str, low: int, high: int) -> pa.Array:
    return arrow_array(low + _pick(ctx, owner, label, high - low))


def _chars(ctx: GenerationContext, owner: str, label: str, alphabet: str) -> pa.Array:
    chars = arrow_array(list(alphabet), type=pa.string())
    return kernel_ops.pool_take(chars, _pick(ctx, owner, label, len(alphabet)))


def _digit(ctx: GenerationContext, owner: str, label: str) -> pa.Array:
    return _chars(ctx, owner, label, "0123456789")


def _group(spec: Mapping[str, Any]) -> str:
    return str(spec.get("group", "address"))


def _place_index(spec: Mapping[str, Any], ctx: GenerationContext, size: int) -> Any:
    return _pick(ctx, _group(spec), "place", size)


def _postcode(country: str, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
    _, _, codes = _arrow_places(country)
    stem = kernel_ops.pool_take(codes, _place_index(spec, ctx, len(codes)))
    owner = _group(spec)
    if country == "GB":  # outward code + inward code: a digit and two letters
        parts = [stem, _digit(ctx, owner, "in1"), _chars(ctx, owner, "in2", LETTERS_GB),
                 _chars(ctx, owner, "in3", LETTERS_GB)]  # fmt: skip
        return kernel_ops.template_strings(
            ["", " ", "", "", ""], [(0, 0), (1, 0), (2, 0), (3, 0)], parts, ctx.n_rows
        )
    if country == "CA":  # forward sortation area + local delivery unit: digit, letter, digit
        parts = [stem, _digit(ctx, owner, "in1"), _chars(ctx, owner, "in2", LETTERS_CA),
                 _digit(ctx, owner, "in3")]  # fmt: skip
        return kernel_ops.template_strings(
            ["", " ", "", "", ""], [(0, 0), (1, 0), (2, 0), (3, 0)], parts, ctx.n_rows
        )
    return stem


def _phone(country: str, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
    n = ctx.n_rows
    owner = ctx.column
    if country in ("US", "CA"):
        # +1 (AAA) 555-01NN: 555-0100 to 555-0199 are the lines the numbering plan keeps for fiction
        return kernel_ops.template_strings(
            ["+1 (", ") 555-01", ""],
            [(0, 0), (1, 2)],
            [_ints(ctx, owner, "area", 200, 1000), _ints(ctx, owner, "line", 0, 100)],
            n,
        )
    if country == "FR":
        international = str(spec.get("format", "national")) == "international"
        roots = [r[1:] if international else r for r in FRENCH_ROOTS]
        root = kernel_ops.pool_take(
            arrow_array(roots, type=pa.string()), _pick(ctx, owner, "root", len(roots))
        )
        prefix = "+33 " if international else ""
        return kernel_ops.template_strings(
            [prefix, " ", " ", ""],
            [(0, 0), (1, 2), (2, 2)],
            [root, _ints(ctx, owner, "mc", 0, 100), _ints(ctx, owner, "du", 0, 100)],
            n,
        )
    raise StrategyError(
        f"locale {country}: no range reserved for fiction is shipped for phone numbers, so the "
        f"phone_number provider is not available ({where(ctx)}); US, CA and FR are"
    )


def _names(country: str, provider: str, ctx: GenerationContext) -> pa.Array:
    if country == "US":
        first = kernel_ops.pool_take(
            _builtin_pool("first_names"),
            _pick(ctx, ctx.column, "first", len(_builtin_pool("first_names"))),
        )
        last = kernel_ops.pool_take(
            _builtin_pool("last_names"),
            _pick(ctx, ctx.column, "last", len(_builtin_pool("last_names"))),
        )
        if provider == "first_name":
            return first
        if provider == "last_name":
            return last
        return kernel_ops.join_strings([first, last], " ")
    needed = ("first_name", "last_name") if provider == "name" else (provider,)
    for part in needed:
        if not (DATA_DIR / f"{country.lower()}_{part}s.txt").exists():
            raise StrategyError(
                f"locale {country}: no openly licensed {part} list is shipped ({where(ctx)}); "
                "see docs/LOCALES.md for the countries and sources"
            )
    first_pool = _arrow_pool(country, "first_names")
    return kernel_ops.pool_take(first_pool, _pick(ctx, ctx.column, "first", len(first_pool)))


class Locale:
    """Basic locale packs: ``spec['locale']`` (a country) and ``spec['provider']`` (``city``,
    ``region``, ``postcode``, ``phone_number``, ``first_name``, ``last_name`` or ``name``).
    ``spec['group']`` (default ``address``) ties the place columns of a table together;
    ``spec['format']`` (``national`` or ``international``) is read by the French phone numbers."""

    name = "locale"

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        raw = spec.get("locale")
        country = normalize(raw)
        if country is None:
            raise StrategyError(
                f"locale strategy needs 'locale' to be one of {', '.join(COUNTRIES)} "
                f"(also fr_FR, fr-FR or fr), not {raw!r} ({where(ctx)})"
            )
        provider = str(spec.get("provider", ""))
        if provider.lower() in NATIONAL_IDENTIFIERS:
            raise StrategyError(
                f"locale strategy does not generate national identifiers ({provider!r}) "
                f"for any country ({where(ctx)})"
            )
        if provider not in PROVIDERS:
            raise StrategyError(
                f"locale strategy has no provider {provider!r} ({where(ctx)}); "
                f"it serves {', '.join(PROVIDERS)}"
            )
        if provider == "phone_number":
            values = _phone(country, spec, ctx)
        elif provider in ("first_name", "last_name", "name"):
            values = _names(country, provider, ctx)
        elif provider == "postcode":
            values = _postcode(country, spec, ctx)
        else:
            city, region, _ = _arrow_places(country)
            column = city if provider == "city" else region
            values = kernel_ops.pool_take(column, _place_index(spec, ctx, len(column)))
        return _truncate(values, ctx)
