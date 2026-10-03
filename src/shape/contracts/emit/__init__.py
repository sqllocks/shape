"""``shape contract emit``: a v1 contract (``shape check``, ``shape.contracts.v1``) as database
DDL, a JSON Schema, a pandera schema or a Great Expectations suite. See ``docs/CONTRACT_EMIT.md``.

``emit(contract, target, **options)`` returns an :class:`EmitResult`: the text, and
``not_expressed``, every rule the target cannot state (``{"table", "column", "rule", "reason"}``;
``column`` is ``None`` for a table-level rule). The same contract and options always give the same
bytes. Where the target has a place for it, the rules left out are kept as metadata, so
:func:`contract_from` can rebuild the expressible part of the contract (``jsonschema`` and ``gx``),
or all of it with ``use_meta=True``. Persisted format: the result, ``shape-contract-emit``
version 1.

The pandera and Great Expectations targets are generated text: emitting never imports pandera or
Great Expectations (the ``ddl`` target imports pyarrow, for the SQL sink's table emitter).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ._common import FORMAT as FORMAT
from ._common import VERSION as VERSION

TARGETS = ("ddl", "jsonschema", "pandera", "gx")
READABLE_TARGETS = ("jsonschema", "gx")
_OPTIONS = {"dialect", "table"}


class EmitError(ValueError):
    """The emit request cannot be served: an unknown target, dialect, option or table."""


@dataclass
class EmitResult:
    """``text`` is the emitted document; ``not_expressed`` lists the rules it leaves out."""

    text: str
    not_expressed: list[dict[str, Any]] = field(default_factory=list)
    target: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": FORMAT,
            "not_expressed": [dict(i) for i in self.not_expressed],
            "target": self.target,
            "text": self.text,
            "version": VERSION,
        }


def _check_request(target: str, options: Mapping[str, Any]) -> None:
    if target not in TARGETS:
        raise EmitError(f"unknown target {target!r}; choose one of {', '.join(TARGETS)}")
    unknown = sorted(set(options) - _OPTIONS)
    if unknown:
        raise EmitError(
            f"unknown option {unknown[0]!r}; the options are {', '.join(sorted(_OPTIONS))}"
        )
    if options.get("dialect") is not None and target != "ddl":
        raise EmitError(f"dialect applies to the ddl target only, not {target}")


def emit(contract: Any, target: str, **options: Any) -> EmitResult:
    """Emit ``contract`` (a dict, or the path of a JSON file) for ``target`` (``ddl``,
    ``jsonschema``, ``pandera`` or ``gx``). Options: ``dialect`` (``ddl`` only: ``tsql``,
    ``tsql-fabric-warehouse``, ``postgres``, ``mysql``; default ``tsql``) and ``table`` (the one
    table of a ``tables`` contract to emit, or the name of a single-table contract's table)."""
    from ._common import Notes, prepare

    _check_request(target, options)
    tables = prepare(contract, options.get("table"))
    notes = Notes()
    if target in READABLE_TARGETS and len(tables) > 1:
        raise EmitError(
            f"the {target} target emits one table; the contract has {', '.join(tables)}: "
            "choose one with --table"
        )
    if target == "ddl":
        from . import ddl

        text = ddl.render(tables, options.get("dialect") or "tsql", notes)
    elif target == "jsonschema":
        from . import jsonschema

        text = jsonschema.render(tables, notes) if tables else _empty(target)
    elif target == "pandera":
        from . import pandera

        text = pandera.render(tables, notes)
    else:
        from . import gx

        text = gx.render(tables, notes) if tables else _empty(target)
    return EmitResult(text=text, not_expressed=notes.result(), target=target)


def _empty(target: str) -> str:
    raise EmitError(f"the {target} target needs a table, and the contract has none")


def contract_from(target: str, text: str, *, use_meta: bool = False) -> dict[str, Any]:
    """The contract an emitted ``jsonschema`` or ``gx`` document states: the expressible part
    (what :func:`expressible` returns for the original), or with ``use_meta`` also the rules kept
    in its metadata, which is the whole normalized contract. The result is one table's contract."""
    if target not in READABLE_TARGETS:
        raise EmitError(
            f"cannot read back the {target} target; the readable targets are "
            f"{', '.join(READABLE_TARGETS)}"
        )
    if target == "jsonschema":
        from . import jsonschema

        return jsonschema.read(text, use_meta)
    from . import gx

    return gx.read(text, use_meta)


def expressible(
    contract: Any, target: str, *, table: str | None = None, dialect: str = "tsql"
) -> dict[str, Any]:
    """The part of ``contract`` that ``target`` states, in the normal form
    :func:`contract_from` returns. A ``tables`` contract gives ``{"tables": {...}}`` unless
    ``table`` names one."""
    from shape.contracts.v1 import _load_contract

    from ._common import prepare

    _check_request(target, {"table": table} if table else {})
    doc = _load_contract(contract)
    tables = prepare(doc, table)
    if target == "ddl":
        from .ddl import check_dialect, expressed_table

        check_dialect(dialect)
        done = {name: expressed_table(sub, dialect) for name, sub in tables.items()}
    elif target == "jsonschema":
        from .jsonschema import expressed_table as jsonschema_part

        done = {name: jsonschema_part(sub) for name, sub in tables.items()}
    elif target == "pandera":
        from .pandera import expressed_table as pandera_part

        done = {name: pandera_part(sub) for name, sub in tables.items()}
    else:
        from .gx import expressed_table as gx_part

        done = {name: gx_part(sub) for name, sub in tables.items()}
    if "tables" in doc and table is None:
        return {"tables": done}
    (only,) = done.values()
    return only
