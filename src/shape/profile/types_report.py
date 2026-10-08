"""The declared-versus-inferred report of a profile (``shape types``, W2-07).

:func:`types_report` reads the ``type_inference`` entries of a stored profile (and, when given, the
type rules of a contract) and lists the columns whose types need a second look:

* ``declared_differs``: a typed source declared a type, and the values say a narrower one is true
  (a ``string`` holding integers, a ``float`` holding whole numbers, a ``string`` holding ISO
  dates);
* ``identifier_suspect``: an integer column that the identifier rule of ``shape.io.identifiers``
  calls a suspect, or would have kept as text (a ZIP code, an NPI, a member number);
* ``low_confidence``: a type inferred from text that fewer than ``min_confidence`` of the values
  fit (a column that is 97% integers, with stray text, is a ``string`` at 0.97 for an integer);
* ``contract_differs``: the contract asks for a type other than the profile's.

A profile written before the records existed has no ``type_inference``: only the contract is
compared for it.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .reference.typeinfer import MIN_CONFIDENCE as MIN_CONFIDENCE

KINDS = ("declared_differs", "identifier_suspect", "low_confidence", "contract_differs")
# declared type -> the narrower types the values may turn out to hold
_NARROWS = {
    "string": ("integer", "float", "boolean", "date", "datetime"),
    "float": ("integer",),
}
_TYPES_OPTION = "--types"
_STRING_OPTION = "--string-columns"


def _check_threshold(value: Any) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or math.isnan(value)
        or not 0.0 <= value <= 1.0
    ):
        raise ValueError(f"min_confidence must be a number from 0 to 1, got {value!r}")
    return float(value)


def _finding(
    table: str,
    column: str,
    kind: str,
    declared: str | None,
    inferred: str | None,
    confidence: float | None,
    option: str,
    message: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "table": table,
        "column": column,
        "kind": kind,
        "declared": declared,
        "inferred": inferred,
        "confidence": confidence,
        "option": option,
        "message": f"{table}.{column}: {message}",
        **extra,
    }


def _from_inference(
    table: str, column: str, ti: Mapping[str, Any], min_confidence: float
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    source, confidence = ti.get("source"), ti.get("confidence")
    profile_type = ti.get("type")
    shares = dict(ti.get("parse_shares") or {})
    base = {"profile_type": profile_type, "source": source, "parse_shares": shares}
    identifier = ti.get("identifier")
    if source == "declared":
        declared, inferred = ti.get("declared"), ti.get("inferred")
        if inferred in _NARROWS.get(declared or "", ()):
            out.append(
                _finding(
                    table, column, "declared_differs", declared, inferred, confidence,
                    _TYPES_OPTION,
                    f"declared {declared}, every value parses as {inferred} "
                    f"(confidence {confidence:.2f}); set the type with {_TYPES_OPTION} "
                    "for a CSV, or where the file is written for a typed file",
                    **base,
                )
            )  # fmt: skip
    if profile_type == "integer" and identifier and source in ("declared", "inferred"):
        out.append(_finish_suspect(table, column, ti, confidence, identifier, base))
    if source == "inferred" and confidence is not None and confidence < min_confidence:
        candidate = ti.get("candidate")
        pct = f"{(1 - confidence) * 100:.4g}%"
        out.append(
            _finding(
                table, column, "low_confidence", None, candidate, confidence, _TYPES_OPTION,
                f"inferred {profile_type}, but {confidence:.2%} of the values parse as "
                f"{candidate} ({pct} do not): set the type with {_TYPES_OPTION} once the other "
                "values are fixed, or keep it as text",
                **base,
            )
        )  # fmt: skip
    return out


def _finish_suspect(
    table: str,
    column: str,
    ti: Mapping[str, Any],
    confidence: float | None,
    identifier: str,
    base: dict[str, Any],
) -> dict[str, Any]:
    return _finding(
        table, column, "identifier_suspect", ti.get("declared") or "integer", "string",
        confidence, _STRING_OPTION,
        f"an integer column that may hold identifiers ({identifier}); a number loses leading "
        f"zeros: keep it as text with {_STRING_OPTION} {column}",
        identifier=identifier, **base,
    )  # fmt: skip


def _contract_columns(profile: Any, contract: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{table: {column: rules}}`` of a contract for the tables of ``profile``."""
    if profile.is_dataset:
        per_table = contract.get("tables")
        if not isinstance(per_table, Mapping):
            from shape.contracts.v1 import ContractError

            raise ContractError(
                "the profile has several tables: give the contract a 'tables' object "
                "mapping table names to contracts"
            )
        return {
            t: dict(sub.get("columns") or {}) for t, sub in per_table.items() if t in profile.tables
        }
    if "tables" in contract:
        from shape.contracts.v1 import ContractError

        raise ContractError(
            "the contract has a 'tables' object but the profile is a single table: "
            "profile the tables together as a dataset"
        )
    (name,) = profile.tables
    return {name: dict(contract.get("columns") or {})}


def types_report(
    profile: Any,
    contract: Mapping[str, Any] | str | Path | None = None,
    min_confidence: float = MIN_CONFIDENCE,
) -> list[dict[str, Any]]:
    """The columns of ``profile`` whose types need a second look, as a list of dicts
    (``table``, ``column``, ``kind``, ``declared``, ``inferred``, ``confidence``, ``option``,
    ``message`` and the evidence). Empty when there is nothing to report.

    ``contract`` (a v1 contract, as a dict or a JSON file) adds the columns whose ``dtype`` rule
    differs from the profile's type; ``min_confidence`` (default 0.99) is the confidence below
    which an inferred type is reported."""
    threshold = _check_threshold(min_confidence)
    wanted: dict[str, dict[str, Any]] = {}
    if contract is not None:
        from shape.contracts.v1 import _load_contract, _validate_contract

        loaded = _load_contract(dict(contract) if isinstance(contract, Mapping) else contract)
        _validate_contract(loaded)
        if profile.is_dataset:
            for sub in (loaded.get("tables") or {}).values():
                _validate_contract(sub)
        wanted = _contract_columns(profile, loaded)
    out: list[dict[str, Any]] = []
    for tname, table in profile.tables.items():
        for cname, col in table["columns"].items():
            ti = col.get("type_inference")
            if ti:
                out.extend(_from_inference(tname, cname, ti, threshold))
            rules = wanted.get(tname, {}).get(cname)
            if rules and "dtype" in rules and rules["dtype"] != col["dtype"]:
                out.append(
                    _finding(
                        tname, cname, "contract_differs", rules["dtype"], col["dtype"],
                        (ti or {}).get("confidence"), _TYPES_OPTION,
                        f"the contract wants {rules['dtype']}, the profile has {col['dtype']}"
                        f"{_confidence_text(ti)}: set the type with {_TYPES_OPTION} or fix the "
                        "contract",
                        profile_type=col["dtype"], source=(ti or {}).get("source"),
                        contract_type=rules["dtype"],
                    )
                )  # fmt: skip
    return out


def _confidence_text(ti: Mapping[str, Any] | None) -> str:
    c = (ti or {}).get("confidence")
    return "" if c is None else f" (confidence {c:.2f})"


def has_records(profile: Any) -> bool:
    """Whether the profile carries ``type_inference`` (one written before W2-07 does not)."""
    return any(
        "type_inference" in c for t in profile.tables.values() for c in t["columns"].values()
    )
