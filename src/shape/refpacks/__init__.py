"""Reference packs: versioned, checksummed reference data that works offline (W3-12).

A pack is a directory with ``pack.json`` (``docs/REFERENCE_PACKS.md``) and its Arrow IPC data
files. :func:`shape.generation.reference.load_dataset` finds the datasets of every pack in the
search paths and in the shipped packs, so the ``reference_pair`` contract rule,
``shape profile --reference-pair`` and the strategies that take a dataset name read them
unchanged.

Stable interface: ``PACK_FORMAT``, ``PACK_VERSION``, ``Pack``, ``Discovery``,
``ReferencePackError``, ``UnknownPackError``, ``discover_packs``, ``find_pack``,
``read_manifest`` and ``write_pack``.
"""

from .packs import (
    PACK_FORMAT,
    PACK_VERSION,
    Discovery,
    Pack,
    ReferencePackError,
    UnknownPackError,
    discover_packs,
    find_pack,
    read_manifest,
    write_pack,
)

__all__ = [
    "PACK_FORMAT",
    "PACK_VERSION",
    "Discovery",
    "Pack",
    "ReferencePackError",
    "UnknownPackError",
    "discover_packs",
    "find_pack",
    "read_manifest",
    "write_pack",
]
