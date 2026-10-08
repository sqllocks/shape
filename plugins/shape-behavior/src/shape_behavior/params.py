"""The parameters document of a primitive (``shape behave run NAME --params FILE.json``).

Persisted format ``shape-behavior-params``, version 1::

    {"format": "shape-behavior-params", "version": 1, "primitive": "telemetry_series",
     "params": {"interval": "1 hour", "missing_rate": 0.02}}

Imports nothing heavy: the command line reads it before any simulation code loads.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

PARAMS_FORMAT = "shape-behavior-params"
PARAMS_VERSION = 1
PRIMITIVE_NAMES = (
    "entity_lifecycle",
    "event_sequence",
    "file_arrival",
    "telemetry_series",
    "transaction_stream",
)
_KEYS = ("format", "version", "primitive", "params")


class PrimitiveParamsError(ValueError):
    """A parameters document names an unknown primitive or parameter, or is not in the format
    (``key`` is the offending key)."""

    def __init__(self, message: str, key: str) -> None:
        super().__init__(message)
        self.key = key


def unknown_primitive(name: Any) -> PrimitiveParamsError:
    return PrimitiveParamsError(
        f"unknown primitive {name!r}; use one of {', '.join(PRIMITIVE_NAMES)}", "primitive"
    )


def check_params_document(doc: Any) -> tuple[str, dict[str, Any]]:
    """The ``(primitive, params)`` of a parameters document, or a :class:`PrimitiveParamsError`
    naming the first key that is wrong. The primitive's own parameters are checked when it is
    built."""
    if not isinstance(doc, dict):
        raise PrimitiveParamsError("the parameters document must be a JSON object", "document")
    if doc.get("format") != PARAMS_FORMAT:
        raise PrimitiveParamsError(
            f"format must be {PARAMS_FORMAT!r}, got {doc.get('format')!r}", "format"
        )
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise PrimitiveParamsError(
            f"version must be a positive integer, got {version!r}", "version"
        )
    if version > PARAMS_VERSION:
        raise PrimitiveParamsError(
            f"version {version} is newer than {PARAMS_VERSION}, the newest this Shape reads; "
            "upgrade sqllocks-shape-behavior to read it",
            "version",
        )
    for key in doc:
        if key not in _KEYS:
            raise PrimitiveParamsError(
                f"unknown key {key!r}; a parameters file has {', '.join(_KEYS)}", key
            )
    primitive = doc.get("primitive")
    if not isinstance(primitive, str) or not primitive:
        raise PrimitiveParamsError(
            f"primitive is required (a name), got {primitive!r}", "primitive"
        )
    if primitive not in PRIMITIVE_NAMES:
        raise unknown_primitive(primitive)
    params = doc.get("params", {})
    if not isinstance(params, dict):
        raise PrimitiveParamsError("params must be an object", "params")
    return primitive, params


def read_params(path: Path) -> tuple[str, dict[str, Any]]:
    """Read and check a parameters file (see :func:`check_params_document`)."""
    return check_params_document(json.loads(Path(path).read_text(encoding="utf-8")))
