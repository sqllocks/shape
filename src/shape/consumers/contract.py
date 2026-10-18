"""The consumer contract format (``shape-consumer-contract``, version 1).

A team that consumes a dataset states the part it depends on::

    {"format": "shape-consumer-contract", "version": 1, "consumer": "finance-reporting",
     "owner": "finance-data@example.com", "source": "orders", "since": "2026-10-03",
     "requires": {"tables": {"orders": {"required_columns": ["order_id", "amount"], ...}}}}

``requires`` is a contract v1 body (``shape check``) limited to what the consumer needs. Unlike a
full contract it never forbids anything: a table or column the consumer does not name is the
producer's business, so ``allow_extra_columns: false`` is rejected.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from shape.contracts.v1 import ContractError, validate_contract
from shape.errors import ShapeError
from shape.schemacheck import validate

FORMAT = "shape-consumer-contract"
VERSION = 1
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_BYTES = 4 << 20
_TABLE_KEYS = (
    "row_count",
    "columns",
    "required_columns",
    "fd",
    "implies",
    "reference_pair",
    "max_implausible_rate",
    "drift",
)


class ConsumerContractError(ShapeError, ValueError):
    """A consumer contract file cannot be read or is not valid. ``problems`` holds every problem
    found, each starting with its key path."""

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.problems = problems or (message,)


@cache
def schema() -> dict[str, Any]:
    text = (
        resources.files("shape")
        .joinpath("schemas/shape-consumer-contract-v1.schema.json")
        .read_text("utf-8")
    )
    loaded: dict[str, Any] = json.loads(text)
    return loaded


@dataclass(frozen=True, slots=True)
class ConsumerContract:
    path: str
    consumer: str
    owner: str
    source: str
    since: str
    requires: dict[str, Any]

    @property
    def tables(self) -> dict[str, Any] | None:
        t = self.requires.get("tables")
        return t if isinstance(t, dict) else None


def _version_problem(doc: dict[str, Any]) -> str | None:
    version = doc.get("version")
    if isinstance(version, int) and not isinstance(version, bool) and version > VERSION:
        return (
            f"$.version: {version} was written by a newer Shape (this one reads up to version "
            f"{VERSION}): upgrade Shape"
        )
    return None


def _body_problems(where: str, body: Any, out: list[str]) -> None:
    """Problems of one contract v1 body (a table, or the whole of a single-table contract)."""
    if not isinstance(body, dict):
        out.append(f"{where}: must be an object")
        return
    unknown = sorted(set(body) - set(_TABLE_KEYS))
    for key in unknown:
        if key == "allow_extra_columns":
            if body[key] is not True:
                out.append(
                    f"{where}.allow_extra_columns: a consumer contract always allows extra "
                    "columns (name only what the consumer depends on)"
                )
        elif key == "tables":
            out.append(f"{where}.tables: tables cannot be nested")
        else:
            out.append(f"{where}.{key}: unknown key")
    for key in _TABLE_KEYS:
        if key not in body or key in ("columns", "drift"):
            continue
        _one(f"{where}.{key}", {key: body[key]}, out)
    columns = body.get("columns")
    if columns is not None:
        if not isinstance(columns, dict):
            out.append(f"{where}.columns: must be an object")
        else:
            for name, rules in columns.items():
                _one(f"{where}.columns.{name}", {"columns": {name: rules}}, out)


def _one(path: str, mini: dict[str, Any], out: list[str]) -> None:
    try:
        validate_contract(mini)
    except ContractError as exc:
        out.append(f"{path}: {exc}")


def problems(doc: Any) -> list[str]:
    """Every problem of a parsed consumer contract, each with its key path (empty: valid)."""
    if not isinstance(doc, dict):
        return ["$: a consumer contract is a JSON object"]
    newer = _version_problem(doc)
    if newer:
        return [newer]
    out = list(validate(doc, schema()))
    for key in ("consumer", "source"):
        value = doc.get(key)
        if isinstance(value, str) and not _NAME.fullmatch(value):
            out.append(
                f"$.{key}: {value!r} is not a valid name (letters, digits, . _ -; it starts "
                "with a letter or digit)"
            )
    for key in ("owner",):
        value = doc.get(key)
        if isinstance(value, str) and not value.strip():
            out.append(f"$.{key}: must not be empty")
    since = doc.get("since")
    if isinstance(since, str):
        try:
            date.fromisoformat(since)
        except ValueError:
            out.append(f"$.since: {since!r} is not a date (use YYYY-MM-DD)")
    requires = doc.get("requires")
    if isinstance(requires, dict):
        tables = requires.get("tables")
        if "tables" in requires:
            for key in sorted(set(requires) - {"tables"}):
                out.append(
                    f"$.requires.{key}: with 'tables', put every rule inside a table "
                    "(or drop 'tables' for a single-table producer)"
                )
            if not isinstance(tables, dict) or not tables:
                out.append("$.requires.tables: must be an object naming at least one table")
            else:
                for name, body in tables.items():
                    _body_problems(f"$.requires.tables.{name}", body, out)
                    if isinstance(body, dict) and not _names_something(body):
                        out.append(f"$.requires.tables.{name}: names no rule")
        else:
            _body_problems("$.requires", requires, out)
            if not _names_something(requires):
                out.append("$.requires: names no table, column or rule")
    return out


def _names_something(body: dict[str, Any]) -> bool:
    return any(body.get(k) for k in _TABLE_KEYS if k != "drift")


def parse(doc: Any, path: str = "<memory>") -> ConsumerContract:
    """Validate a consumer contract document and return its parsed representation."""
    found = problems(doc)
    if found:
        raise ConsumerContractError(f"{path}: not a valid consumer contract", tuple(found))
    return ConsumerContract(
        path=path,
        consumer=doc["consumer"],
        owner=doc["owner"],
        source=doc["source"],
        since=doc["since"],
        requires=doc["requires"],
    )


def load(path: str | Path) -> ConsumerContract:
    """Read and validate one consumer contract file (JSON)."""
    p = Path(path)
    try:
        if p.is_dir():  # Windows reports reading a folder as PermissionError, not this
            raise IsADirectoryError(path)
        if p.stat().st_size > _MAX_BYTES:
            raise ConsumerContractError(f"{path}: too large for a consumer contract")
        doc = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise ConsumerContractError(f"{path}: file not found") from None
    except IsADirectoryError:
        raise ConsumerContractError(f"{path}: is a folder, not a file") from None
    except UnicodeDecodeError:
        raise ConsumerContractError(f"{path}: is not a text file") from None
    except json.JSONDecodeError as exc:
        raise ConsumerContractError(
            f"{path}: not valid JSON: {exc}",
            (f"$: not valid JSON (line {exc.lineno}, column {exc.colno}): {exc.msg}",),
        ) from exc
    return parse(doc, str(p))
