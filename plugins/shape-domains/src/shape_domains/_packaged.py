"""Shared by the domains that ship a 3nf and a star schema plus Arrow reference datasets."""

from __future__ import annotations

import json
from functools import cache
from importlib import resources
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.plugins.api.v1 import DomainDefinition

_PACKAGE = "shape_domains"
_FILES = {"3nf": "schema.json", "star": "schema_star.json"}


@cache
def schema_document(domain: str, mode: str) -> dict[str, Any]:
    text = resources.files(_PACKAGE).joinpath(f"data/{domain}/{_FILES[mode]}").read_text("utf-8")
    document: dict[str, Any] = json.loads(text)
    return document


@cache
def reference_table(domain: str, name: str) -> pa.Table:
    path = resources.files(_PACKAGE).joinpath(f"data/{domain}/reference/{name}.arrow")
    with path.open("rb") as handle:
        table: pa.Table = pa.ipc.open_file(handle).read_all()
    return table


class PackagedDomain:
    """A ``shape.domains`` entry: ``name``, the datasets it ships (``datasets``) and the datasets
    it borrows from another domain's data (``borrowed``: dataset name to ``(domain, name)``)."""

    name = ""
    datasets: tuple[str, ...] = ()
    borrowed: dict[str, tuple[str, str]] = {}
    modes = ("3nf", "star")

    def definition(self, mode: str = "3nf") -> DomainDefinition:
        if mode not in _FILES:
            raise ValueError(f"{self.name} has no {mode!r} mode (3nf, star)")
        schema = schema_document(self.name, mode)
        reference = {d: reference_table(self.name, d) for d in self.datasets}
        for dataset, (domain, source) in self.borrowed.items():
            reference[dataset] = reference_table(domain, source)
        return DomainDefinition(
            schema=schema,
            reference_data=reference,
            scale_presets={k: dict(v) for k, v in schema["generation"]["scales"].items()},
        )
