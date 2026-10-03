"""``shape learn``: a profile in, a generation schema out.

:class:`SchemaBuilder` maps every column of a profile to the strategy that generates it
(primary key, foreign key, pattern, date, enum, numeric distribution, string), derives scale presets
from the row counts and lists the correlated column pairs. The result is a
:class:`~shape.generation.schema.GenSchema` that ``shape generate`` runs as it is.

Some rules go beyond the obvious mapping, and :data:`DIFFERENCES` names each one with its reason
(the parity harness under ``benchmarks/`` checks, column by column, that nothing else differs from
the reference builder the rules were ported from, and ``tests/generation/test_learn.py`` reads the
list):

* ``truncated_enum``: a numeric column with more distinct values than its profile lists (the top
  500) is not an enum.
* ``exponential``: a column fitted as an exponential distribution is generated as one.
* ``enum_pattern``: a string column of currency or language codes whose few values are all listed
  in the profile keeps those values.
* ``shifted_lognormal``: a log-normal fit with a material location shift is generated from the
  column's quantiles.

Stable interface: :class:`SchemaBuilder`, :func:`profile_from_dict`, :func:`learn`,
:data:`DIFFERENCES`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shape.generation.schema import GenSchema
from shape.profile.reference.model import ColumnProfile, DatasetProfile, TableProfile

DIFFERENCES: dict[str, str] = {
    "truncated_enum": (
        "a numeric column whose profile lists fewer values than it has distinct ones is generated "
        "from its distribution; drawing only the listed values (the 500 most frequent) would "
        "leave the rest of the column's values out for good"
    ),
    "enum_pattern": (
        "a currency or language code column whose profile lists every value is generated from "
        "those values and weights; drawing any code of a fake-data provider would lose the "
        "column's own vocabulary (and needs the optional faker package). Other patterns "
        "(e-mail, phone, ip address...) are never turned into value lists: the schema would "
        "carry personal data"
    ),
    "shifted_lognormal": (
        "a log-normal fit whose location shift is more than 2% of the column's level is generated "
        "from the column's quantiles; the log-normal generator has no location parameter, so "
        "dropping the shift puts the values far from the column's range, clipped to its maximum"
    ),
    "exponential": (
        "an exponential fit becomes an exponential distribution; the alternative, a normal with "
        "the same mean and standard deviation clipped to the observed range, has the wrong shape"
    ),
}

# Faker provider guesses for string columns without a recognised pattern, by column name.
_FAKER_NAME_HINTS: dict[str, str] = {
    "name": "name",
    "first_name": "first_name",
    "last_name": "last_name",
    "full_name": "name",
    "email": "email",
    "phone": "phone_number",
    "phone_number": "phone_number",
    "address": "street_address",
    "street": "street_address",
    "city": "city",
    "state": "state",
    "zip": "zipcode",
    "zipcode": "zipcode",
    "zip_code": "zipcode",
    "postal_code": "zipcode",
    "country": "country",
    "company": "company",
    "company_name": "company",
    "url": "url",
    "website": "url",
    "username": "user_name",
    "user_name": "user_name",
    "description": "sentence",
    "comment": "sentence",
    "notes": "paragraph",
    "title": "catch_phrase",
    "job": "job",
    "job_title": "job",
    "ssn": "ssn",
    "sku": "bothify",
    "color": "color_name",
    "ip": "ipv4",
    "ip_address": "ipv4",
}

_PATTERN_PROVIDERS: dict[str, str] = {
    "ssn": "ssn",
    "ip_address": "ipv4",
    "mac_address": "mac_address",
    "iban": "iban",
    "postal_code": "postcode",
    "currency_code": "currency_code",
    "language_code": "language_code",
}

_CODE_PATTERNS = ("currency_code", "language_code")

_COLUMN_TYPES = {
    "integer": "integer",
    "float": "decimal",
    "string": "string",
    "date": "date",
    "datetime": "datetime",
    "boolean": "boolean",
}

_DOW_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


# ---- profile documents ----------------------------------------------------------------------


def _untag(value: Any) -> Any:
    """``["int", 5]`` (a profile's tagged minimum or maximum) as ``5``."""
    if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
        return value[1]
    return value


def _column(doc: Mapping[str, Any]) -> ColumnProfile:
    value_counts = doc.get("value_counts_ext")
    order = doc.get("value_counts_ext_order")
    if value_counts and order:
        value_counts = {k: value_counts[k] for k in order if k in value_counts}
    return ColumnProfile(
        name=doc["name"],
        dtype=doc["dtype"],
        null_count=doc.get("null_count", 0),
        null_rate=doc.get("null_rate"),
        cardinality=doc.get("cardinality", 0),
        cardinality_ratio=doc.get("cardinality_ratio"),
        is_unique=doc.get("is_unique"),
        is_enum=bool(doc.get("is_enum")),
        enum_values=doc.get("enum_values"),
        min_value=_untag(doc.get("min_value")),
        max_value=_untag(doc.get("max_value")),
        mean=doc.get("mean"),
        std=doc.get("std"),
        distribution=doc.get("distribution"),
        distribution_params=doc.get("distribution_params"),
        pattern=doc.get("pattern"),
        is_primary_key=bool(doc.get("is_primary_key")),
        is_foreign_key=bool(doc.get("is_foreign_key")),
        fk_ref_table=doc.get("fk_ref_table"),
        quantiles=doc.get("quantiles"),
        hour_histogram=doc.get("hour_histogram"),
        dow_histogram=doc.get("dow_histogram"),
        temporal_histogram=doc.get("temporal_histogram"),
        string_length=doc.get("string_length"),
        outlier_rate=doc.get("outlier_rate"),
        value_counts_ext=value_counts,
        fit_score=doc.get("fit_score"),
        nan_count=int(doc.get("nan_count") or 0),
        inf_count=int(doc.get("inf_count") or 0),
        pattern_rates=doc.get("pattern_rates"),
        pattern_contains_rates=doc.get("pattern_contains_rates"),
        precision=doc.get("precision"),
        scale=doc.get("scale"),
        placeholders=doc.get("placeholders"),
    )


def _table(doc: Mapping[str, Any]) -> TableProfile:
    return TableProfile(
        name=doc["name"],
        row_count=int(doc["row_count"]),
        columns={n: _column(c) for n, c in doc["columns"].items()},
        primary_key=list(doc.get("primary_key") or []),
        detected_fks=dict(doc.get("detected_fks") or {}),
        correlation_matrix=doc.get("correlation_matrix"),
        correlation_truncated=bool(doc.get("correlation_truncated")),
        joint=doc.get("joint"),
    )


def profile_from_dict(doc: Mapping[str, Any]) -> DatasetProfile:
    """A table or dataset profile document (``Profile.to_dict()``) as a :class:`DatasetProfile`
    with plain minimum and maximum values."""
    if "tables" in doc:
        return DatasetProfile(
            tables={n: _table(t) for n, t in doc["tables"].items()},
            relationships=[dict(r) for r in doc.get("relationships") or []],
        )
    return DatasetProfile(tables={doc["name"]: _table(doc)}, relationships=[])


def as_dataset(profile: Any) -> DatasetProfile:
    """A :class:`DatasetProfile` from a ``Profile``, its ``to_dict()`` or a ``DatasetProfile``."""
    if isinstance(profile, DatasetProfile):
        return profile
    doc = profile.to_dict() if hasattr(profile, "to_dict") else profile
    return profile_from_dict(doc)


# ---- the builder ----------------------------------------------------------------------------


def _is_covered_enum(col: ColumnProfile) -> bool:
    """Whether the profile lists every distinct value of the column."""
    values = col.value_counts_ext or col.enum_values
    return values is not None and len(values) > 0 and len(values) >= col.cardinality


def _zero_padded_width(col: ColumnProfile) -> int:
    """The width of a text column whose values are all the same number of digits and some start
    with a zero (so that read as numbers they would lose their zeros), else 0."""
    if col.dtype != "string" or col.is_foreign_key or not col.string_length:
        return 0
    low, high = col.string_length.get("min"), col.string_length.get("max")
    if low is None or low != high or not 2 <= low <= 18:
        return 0
    lo, hi = _text_of(col.min_value), _text_of(col.max_value)
    if (
        lo is None
        or hi is None
        or not (lo.isascii() and lo.isdigit() and hi.isascii() and hi.isdigit())
    ):
        return 0
    return int(low) if lo.startswith("0") else 0


def _text_of(value: Any) -> str | None:
    return value if isinstance(value, str) else None


def guess_provider(column_name: str) -> str:
    """A faker provider guessed from a column name."""
    lower = column_name.lower().strip()
    if lower in _FAKER_NAME_HINTS:
        return _FAKER_NAME_HINTS[lower]
    for hint, provider in _FAKER_NAME_HINTS.items():
        if lower.endswith(f"_{hint}") or lower.endswith(hint):
            return provider
    for word, provider in (
        ("email", "email"),
        ("phone", "phone_number"),
        ("name", "name"),
        ("addr", "street_address"),
        ("city", "city"),
        ("state", "state"),
        ("country", "country"),
        ("date", "date"),
    ):
        if word in lower:
            return provider
    if "url" in lower or "link" in lower:
        return "url"
    return "pystr"


def translate_distribution(
    name: str | None, params: Mapping[str, float] | None
) -> tuple[str, dict[str, float]] | None:
    """A fitted distribution (scipy names, ``loc``, ``scale``, ``s``) as a generation strategy's
    family and parameters, or ``None`` when no family matches."""
    import math

    if not name or not params:
        return None
    loc, scale = params.get("loc"), params.get("scale")
    if name == "normal" and loc is not None and scale is not None:
        return "normal", {"mean": float(loc), "std_dev": max(float(scale), 1e-9)}
    if name == "uniform" and loc is not None and scale is not None:
        return "uniform", {"min": float(loc), "max": float(loc) + float(scale)}
    if name == "lognormal":
        s = params.get("s")
        if s is None or scale is None or scale <= 0:
            return None
        return "log_normal", {"mean": float(math.log(scale)), "sigma": max(float(s), 1e-9)}
    return None


def _exponential(col: ColumnProfile) -> dict[str, Any] | None:
    """The ``exponential`` rule (:data:`DIFFERENCES`): an exponential fit with a negligible shift
    is generated as an exponential distribution."""
    params = col.distribution_params
    if col.distribution != "exponential" or not params:
        return None
    scale, loc = params.get("scale"), params.get("loc", 0.0)
    if scale is None or scale <= 0 or abs(loc) > 0.01 * scale:
        return None
    # the rate from the sample mean (the fitted scale can sit a little off it)
    mean = (col.mean - loc) if col.mean is not None and col.mean - loc > 0 else float(scale)
    spec: dict[str, float] = {"lambda": 1.0 / float(mean)}
    if col.min_value is not None:
        spec["min"] = float(col.min_value)
    if col.max_value is not None:
        spec["max"] = float(col.max_value)
    return {"strategy": "distribution", "distribution": "exponential", "params": spec}


def _shifted_lognormal(col: ColumnProfile) -> bool:
    """The ``shifted_lognormal`` rule (:data:`DIFFERENCES`)."""
    params = col.distribution_params
    if col.distribution != "lognormal" or not params:
        return False
    level = max(abs(col.mean or 0.0), abs(col.std or 0.0))
    return abs(params.get("loc", 0.0)) > 0.02 * level


class SchemaBuilder:
    """A dataset profile as a generation schema (see the module docstring)."""

    def build(
        self,
        profile: Any,
        domain_name: str = "inferred",
        fit_threshold: float = 0.80,
        correlation_threshold: float = 0.5,
    ) -> GenSchema:
        dataset = as_dataset(profile)
        parent_pk = {n: t.primary_key[0] for n, t in dataset.tables.items() if t.primary_key}

        tables: dict[str, Any] = {}
        for tname, tp in dataset.tables.items():
            columns: dict[str, Any] = {}
            for cname, cp in tp.columns.items():
                null_rate = float(cp.null_rate or 0.0)
                columns[cname] = {
                    "name": cname,
                    "type": _COLUMN_TYPES.get(cp.dtype, "string"),
                    "generator": self.column_generator(cp, parent_pk, fit_threshold),
                    "nullable": null_rate > 0,
                    "null_rate": null_rate,
                    "max_length": None,
                    "precision": None,
                    "scale": None,
                }
            pk = list(tp.primary_key)
            if not pk:
                # No key detected: add a surrogate so the schema validates and generates.
                columns["_row_id"] = {
                    "name": "_row_id",
                    "type": "integer",
                    "generator": {"strategy": "sequence", "start": 1},
                    "nullable": False,
                    "null_rate": 0.0,
                    "max_length": None,
                    "precision": None,
                    "scale": None,
                }
                pk = ["_row_id"]
            tables[tname] = {
                "name": tname,
                "description": f"Inferred from {tp.row_count} rows",
                "cdm_mapping": None,
                "primary_key": pk,
                "columns": columns,
            }

        document: dict[str, Any] = {
            "schema_version": "1",
            "model": {
                "name": f"{domain_name}_inferred",
                "description": f"Schema inferred from existing data ({len(tables)} tables)",
                "domain": domain_name,
                "schema_mode": "3nf",
                "locale": "en_US",
                "seed": 42,
                "date_range": {},
            },
            "tables": tables,
            "relationships": [self._relationship(r) for r in dataset.relationships],
            "business_rules": [],
            "generation": self._scales(dataset),
            "correlated_columns": self._correlated(dataset, correlation_threshold),
        }
        from shape.generation.schema import SCHEMA_VERSION

        document["schema_version"] = SCHEMA_VERSION
        return GenSchema.from_dict(document)

    # ---- columns ------------------------------------------------------------------------

    def column_generator(
        self, col: ColumnProfile, parent_pk: Mapping[str, str], fit_threshold: float = 0.80
    ) -> dict[str, Any]:
        """The generator of one column: the first rule that applies."""
        width = _zero_padded_width(col)
        if width:  # an identifier kept as text (ZIP, NDC, member id): never a number or a pattern
            if col.is_primary_key or col.is_unique:
                return {"strategy": "faker", "provider": "digit_ids", "width": width}
            return {"strategy": "faker", "provider": "digits", "width": width}
        if col.is_primary_key:
            if col.pattern == "uuid" or col.dtype == "string":
                return {"strategy": "uuid"}
            return {
                "strategy": "sequence",
                "start": int(col.min_value) if col.min_value is not None else 1,
            }
        if col.is_foreign_key and col.fk_ref_table:
            key = parent_pk.get(col.fk_ref_table, f"{col.fk_ref_table}_id")
            return {"strategy": "foreign_key", "ref": f"{col.fk_ref_table}.{key}"}
        if col.pattern == "uuid":
            return {"strategy": "uuid"}
        if col.pattern == "email":
            return {"strategy": "faker", "provider": "email"}
        if col.pattern == "phone":
            return {"strategy": "faker", "provider": "phone_number"}
        if col.pattern in _PATTERN_PROVIDERS:
            if (
                col.pattern in _CODE_PATTERNS
                and col.is_enum
                and col.dtype == "string"
                and _is_covered_enum(col)
            ):
                return {"strategy": "weighted_enum", "values": dict(col.value_counts_ext or {})}
            return {"strategy": "faker", "provider": _PATTERN_PROVIDERS[col.pattern]}
        if col.pattern == "date":
            return {"strategy": "temporal", "type": "date"}
        if col.dtype in ("date", "datetime"):
            return self._temporal(col)

        numeric = col.dtype in ("integer", "float")
        if col.is_enum and not (numeric and not _is_covered_enum(col)):  # truncated_enum
            values = col.value_counts_ext if col.value_counts_ext else col.enum_values
            if values:
                return {"strategy": "weighted_enum", "values": dict(values)}
        if col.dtype == "boolean":
            return {"strategy": "weighted_enum", "values": {"true": 0.5, "false": 0.5}}
        if numeric:
            return self._numeric(col, fit_threshold)
        if col.dtype == "string":
            provider = guess_provider(col.name)
            if col.string_length:
                limit = int(col.string_length.get("p95", col.string_length.get("max", 255)))
                return {"strategy": "faker", "provider": provider, "max_length": limit}
            return {"strategy": "faker", "provider": provider}
        return {"strategy": "faker", "provider": "pystr"}

    @staticmethod
    def _temporal(col: ColumnProfile) -> dict[str, Any]:
        gen: dict[str, Any] = {"strategy": "temporal", "type": col.dtype}
        if col.min_value is not None:
            gen["start"] = str(col.min_value)
        if col.max_value is not None:
            gen["end"] = str(col.max_value)
        if col.hour_histogram or col.dow_histogram:
            gen["pattern"] = "seasonal"
            profiles: dict[str, Any] = {}
            if col.hour_histogram:
                profiles["hour_of_day"] = {str(h): w for h, w in enumerate(col.hour_histogram)}
            if col.dow_histogram:
                profiles["day_of_week"] = {
                    _DOW_NAMES[i]: w for i, w in enumerate(col.dow_histogram)
                }
            gen["profiles"] = profiles
        return gen

    @staticmethod
    def _numeric(col: ColumnProfile, fit_threshold: float) -> dict[str, Any]:
        if col.fit_score is not None and col.fit_score < fit_threshold and col.quantiles:
            return {"strategy": "empirical", "quantiles": dict(col.quantiles)}
        exponential = _exponential(col)
        if exponential is not None:
            return exponential
        if _shifted_lognormal(col) and col.quantiles:
            return {"strategy": "empirical", "quantiles": dict(col.quantiles)}
        translated = translate_distribution(col.distribution, col.distribution_params)
        if translated is not None:
            family, params = translated
            if col.min_value is not None:
                params.setdefault("min", float(col.min_value))
            if col.max_value is not None:
                params.setdefault("max", float(col.max_value))
            return {"strategy": "distribution", "distribution": family, "params": params}
        if col.mean is not None and col.std is not None:
            params = {"mean": float(col.mean), "std_dev": max(float(col.std), 0.01)}
            if col.min_value is not None:
                params["min"] = float(col.min_value)
            if col.max_value is not None:
                params["max"] = float(col.max_value)
            return {"strategy": "distribution", "distribution": "normal", "params": params}
        if col.min_value is not None and col.max_value is not None:
            return {
                "strategy": "distribution",
                "distribution": "uniform",
                "params": {"min": float(col.min_value), "max": float(col.max_value)},
            }
        return {
            "strategy": "distribution",
            "distribution": "uniform",
            "params": {"min": 0, "max": 1},
        }

    # ---- the rest of the schema ---------------------------------------------------------

    @staticmethod
    def _relationship(rel: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "name": rel["name"],
            "parent": rel["parent"],
            "child": rel["child"],
            "parent_columns": list(rel["parent_columns"]),
            "child_columns": list(rel["child_columns"]),
            "type": rel.get("type", "one_to_many"),
            "cardinality": {},
            "optional": False,
        }

    @staticmethod
    def _scales(dataset: DatasetProfile) -> dict[str, Any]:
        small: dict[str, int] = {}
        medium: dict[str, int] = {}
        large: dict[str, int] = {}
        for tname, tp in dataset.tables.items():
            small[tname] = max(tp.row_count, 100)
            medium[tname] = max(tp.row_count * 10, 1000)
            large[tname] = max(tp.row_count * 100, 10000)
        return {
            "scale": "small",
            "scales": {"small": small, "medium": medium, "large": large},
            "derived_counts": {},
            "output": {},
        }

    @staticmethod
    def _correlated(dataset: DatasetProfile, threshold: float) -> dict[str, list[list[Any]]]:
        out: dict[str, list[list[Any]]] = {}
        for tname, tp in dataset.tables.items():
            if not tp.correlation_matrix:
                continue
            pairs: list[list[Any]] = []
            seen: set[frozenset[str]] = set()
            for a, row in tp.correlation_matrix.items():
                for b, r in row.items():
                    key = frozenset((a, b))
                    if a == b or key in seen:
                        continue
                    seen.add(key)
                    if abs(r) >= threshold:
                        pairs.append([a, b, r])
            if pairs:
                out[tname] = pairs
        return out


def learn(profile: Any, domain: str = "inferred") -> GenSchema:
    """The generation schema of a profile (``SchemaBuilder().build``)."""
    return SchemaBuilder().build(profile, domain_name=domain)


__all__ = [
    "DIFFERENCES",
    "SchemaBuilder",
    "as_dataset",
    "guess_provider",
    "learn",
    "profile_from_dict",
    "translate_distribution",
]
