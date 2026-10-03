"""Shared by the domains that ship a 3nf and a star schema plus Arrow reference datasets.

The data files are read with plain file I/O when the package is on disk (``importlib.resources``
costs about 0.8 ms in a fresh process), and the Arrow reference files are read from memory, not
through a Python file object: together they were 3 of the 5.5 ms of loading ``hr``. A schema whose
SHA-256 digest is the one recorded in ``_digests`` (written by ``scripts/update_domain_digests.py``
after the schema was checked against ``generation-schema-v1.json``, and enforced by a test) is
handed to the host as ``validated``, so that the host does not check it again; any other content,
or a ``generation-schema-v1.json`` that is not the one the digests were made against, is checked."""

from __future__ import annotations

import hashlib
import json
import os
from functools import cache
from importlib import resources
from typing import TYPE_CHECKING, Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.plugins.api.v1 import DomainDefinition

if TYPE_CHECKING:
    from shape.generation.composite import Composition

_PACKAGE = "shape_domains"
_FILES = {"3nf": "schema.json", "star": "schema_star.json"}
_DIR = os.path.dirname(os.path.abspath(__file__))
# Files above this size are memory-mapped (no copy); smaller ones are read into memory, which is
# several times faster for the 1 to 3 kB reference tables.
_MAP_ABOVE = 1 << 20


def _read_bytes(relative: str) -> bytes:
    """The bytes of ``data/<relative>``."""
    try:
        with open(os.path.join(_DIR, "data", relative), "rb") as handle:
            return handle.read()
    except OSError:  # a zipped install
        return resources.files(_PACKAGE).joinpath(f"data/{relative}").read_bytes()


def read_ipc(relative: str) -> pa.Table:
    """The Arrow IPC file ``data/<relative>`` as a table."""
    path = os.path.join(_DIR, "data", relative)
    try:
        if os.path.getsize(path) > _MAP_ABOVE:
            with pa.memory_map(path) as source:
                table: pa.Table = pa.ipc.open_file(source).read_all()
            return table
    except OSError:  # a zipped install: no such file on disk
        pass
    table = pa.ipc.open_file(pa.BufferReader(_read_bytes(relative))).read_all()
    return table


def schema_digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


@cache
def _schema_entry(domain: str, mode: str) -> tuple[dict[str, Any], bool]:
    """The parsed schema document, and whether its digest is on record (see the module doc)."""
    from shape.generation.schema import json_schema_digest
    from shape_domains import _digests

    relative = f"{domain}/{_FILES[mode]}"
    content = _read_bytes(relative)
    document: dict[str, Any] = json.loads(content.decode("utf-8"))
    recorded = (
        _digests.SCHEMAS.get(relative) == schema_digest(content)
        and _digests.JSON_SCHEMA == json_schema_digest()
    )
    return document, recorded


def schema_document(domain: str, mode: str) -> dict[str, Any]:
    return _schema_entry(domain, mode)[0]


def is_validated(domain: str, mode: str) -> bool:
    """Whether the schema document of ``domain`` in ``mode`` is the one that was checked."""
    return _schema_entry(domain, mode)[1]


@cache
def reference_table(domain: str, name: str) -> pa.Table:
    return read_ipc(f"{domain}/reference/{name}.arrow")


class PackagedDomain:
    """A ``shape.domains`` entry: ``name``, the datasets it ships (``datasets``) and the datasets
    it borrows from another domain's data (``borrowed``: dataset name to ``(domain, name)``)."""

    name = ""
    datasets: tuple[str, ...] = ()
    borrowed: dict[str, tuple[str, str]] = {}
    modes = ("3nf", "star")

    def composition(self) -> Composition:
        """The composite presets and shared-entity tables (``shape composite``). Imported here, not
        with the module: loading a domain to generate it does not need the composite machinery
        (about 2.5 ms in a fresh process)."""
        from shape_domains.composition import composition

        return composition()

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
            validated=is_validated(self.name, mode),
        )
