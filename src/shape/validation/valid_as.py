"""Column validators: is each value a valid IBAN, ISO 3166 / 4217 / 639-1 code or US ZIP? (W3-12)

``validator(kind)`` is a function from a value to ``True`` or ``False``; ``measure(kind, values)``
counts the values it accepts. ``shape.profile(..., validators=)`` stores the count on the column
(``validators: {KIND: {checked, valid, valid_rate}}``, no values), and the contract rule
``valid_as`` reads it.

Except for the IBAN (spaces are ignored and case folded, as IBANs are written), a code must be
written exactly: ISO 3166 and 4217 codes in upper case, ISO 639-1 codes in lower case, no
surrounding spaces. A ZIP is five digits, optionally followed by ``-`` and four digits, and not
``00000`` (no ZIP has it); a number from 1 to 99999 counts as a ZIP whose leading zeros were lost
when the column was read as numbers. Code lists come from the reference packs
(``docs/REFERENCE_PACKS.md``); ``iso4217`` needs the ``iso-4217`` pack, which Shape does not ship.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable
from typing import Any

from shape.errors import ShapeError

from .iban import is_valid_iban

__all__ = ["KINDS", "ValidatorUnavailableError", "measure", "validator"]

KINDS = ("iban", "iso3166_alpha2", "iso3166_alpha3", "iso4217", "iso639_1", "us_zip")

_ZIP = re.compile(r"(\d{5})(?:-\d{4})?")

#: (dataset, field, how to get the pack when it is missing) of each code-list validator.
_CODE_LISTS = {
    "iso3166_alpha2": ("iso_3166_1", "alpha2", None),
    "iso3166_alpha3": ("iso_3166_1", "alpha3", None),
    "iso639_1": ("iso_639_1", "alpha2", None),
    "iso4217": (
        "iso_4217",
        "alphabetic_code",
        "Shape does not ship the iso-4217 pack (its publisher states no licence that allows "
        "redistribution): build it from your own copy of the list with "
        "`python scripts/build_reference_packs.py iso-4217 --source list-one.xml --out DIR` "
        "and put DIR's parent on SHAPE_REFERENCE_PATH (docs/REFERENCE_PACKS.md)",
    ),
}


class ValidatorUnavailableError(ShapeError):
    """The data a validator needs is not available (a reference pack that is not installed)."""


def _codes(kind: str) -> frozenset[str]:
    from shape.generation.reference import DatasetNotFoundError, load_dataset

    dataset, field, hint = _CODE_LISTS[kind]
    try:
        ds = load_dataset(dataset)
    except DatasetNotFoundError as exc:
        raise ValidatorUnavailableError(
            f"the {kind} validator needs the reference dataset {dataset!r}: "
            f"{hint or 'is a reference pack missing? (shape reference list)'}"
        ) from exc
    return frozenset(v for v in ds.column(field).to_pylist() if v is not None)


def _zip(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # a whole number held as a float (a column of numbers with nulls)
    if isinstance(value, int):
        # A ZIP read as a number has lost its leading zeros (2872 was 02872): a number from 1 to
        # 99999 is accepted as such. Profile the column as text to check the zeros too.
        return 1 <= value <= 99999
    if not isinstance(value, str):
        return False
    found = _ZIP.fullmatch(value)
    return found is not None and found.group(1) != "00000"


def validator(kind: str) -> Callable[[Any], bool]:
    """The check for ``kind`` (one of :data:`KINDS`); raises :class:`ValueError` for any other
    and :class:`ValidatorUnavailableError` when the code list it needs is not installed."""
    if kind == "iban":
        return is_valid_iban
    if kind == "us_zip":
        return _zip
    if kind not in _CODE_LISTS:
        raise ValueError(f"unknown validator kind {kind!r}; the kinds are {', '.join(KINDS)}")
    codes = _codes(kind)
    return lambda value: isinstance(value, str) and value in codes


def _is_missing(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def measure(kind: str, values: Iterable[Any]) -> dict[str, Any]:
    """``{"checked", "valid", "valid_rate"}`` for ``values``: missing values (``None``, NaN) are
    not checked; ``valid_rate`` is ``None`` when nothing was checked."""
    return measure_counts(kind, ((v, 1) for v in values))


def measure_counts(kind: str, counts: Iterable[tuple[Any, int]]) -> dict[str, Any]:
    """As :func:`measure`, for ``(value, how many)`` pairs (a column's distinct values)."""
    check = validator(kind)
    checked = valid = 0
    for value, n in counts:
        if _is_missing(value):
            continue
        checked += n
        if check(value):
            valid += n
    return {
        "checked": checked,
        "valid": valid,
        "valid_rate": round(valid / checked, 6) if checked else None,
    }


def normalise_specs(specs: Any) -> dict[str, list[str]]:
    """``{column: kind or [kinds]}`` as ``{column: [kinds]}``; anything else is a
    :class:`ValueError`."""
    if not isinstance(specs, dict):
        raise ValueError("validators is a dict of column name to a kind or a list of kinds")
    out: dict[str, list[str]] = {}
    for column, kinds in specs.items():
        if isinstance(kinds, str):
            kinds = [kinds]
        if not (
            isinstance(kinds, (list, tuple)) and kinds and all(isinstance(k, str) for k in kinds)
        ):
            raise ValueError(
                f"validators: column {column!r} needs a kind or a non-empty list of kinds "
                f"({', '.join(KINDS)})"
            )
        out[str(column)] = list(kinds)
    return out


def _distinct(arr: Any) -> list[tuple[Any, int]]:
    import pyarrow as pa  # type: ignore[import-untyped]
    import pyarrow.compute as pc  # type: ignore[import-untyped]

    if not isinstance(arr, (pa.Array, pa.ChunkedArray)):
        arr = pa.array(arr, from_pandas=True)
    counts = pc.value_counts(arr)
    return list(
        zip(counts.field("values").to_pylist(), counts.field("counts").to_pylist(), strict=True)
    )


def attach_validators(table_doc: dict[str, Any], cols: Iterable[Any], specs: Any) -> None:
    """Measure each requested validator and store it on its column of ``table_doc`` (a table's
    profile document): ``validators: {kind: {checked, valid, valid_rate}}``. ``cols`` are the
    columns that were profiled (each has ``name`` and ``arr``)."""
    if not specs:
        return
    wanted = normalise_specs(specs)
    by_name = {c.name: c for c in cols}
    for column in wanted:
        if column not in by_name:
            raise ValueError(f"validators: the data has no column {column!r}")
    for column, kinds in wanted.items():
        for kind in kinds:
            validator(kind)  # fail on an unknown kind or a missing code list before any work
        counts = _distinct(by_name[column].arr)
        entry = table_doc["columns"][column].setdefault("validators", {})
        for kind in kinds:
            entry[kind] = measure_counts(kind, counts)
