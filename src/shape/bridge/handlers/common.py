"""Shared pieces of the handlers: targets, JSON conversion and result-schema builders."""

from __future__ import annotations

import base64
import datetime as dt
import decimal
import math
from typing import TYPE_CHECKING, Any

from shape.bridge.protocol import BridgeError

if TYPE_CHECKING:
    from shape.generation.schema import GenSchema

# ---- result schema builders ---------------------------------------------------------------

STR: dict[str, Any] = {"type": "string"}
INT: dict[str, Any] = {"type": "integer"}
NUM: dict[str, Any] = {"type": "number"}
BOOL: dict[str, Any] = {"type": "boolean"}
ANY: dict[str, Any] = {}
STRS: dict[str, Any] = {"type": "array", "items": STR}
SPILLED: dict[str, Any] = {
    "type": "object",
    "properties": {
        "spilled": {"const": True},
        "content_id": STR,
        "path": STR,
        "bytes": INT,
    },
    "required": ["spilled", "content_id", "path", "bytes"],
}


def obj(
    required: dict[str, Any] | None = None, optional: dict[str, Any] | None = None
) -> dict[str, Any]:
    """An object schema. A result may gain fields in a minor version, so unknown ones are
    allowed (a client ignores what it does not know)."""
    props = {**(required or {}), **(optional or {})}
    out: dict[str, Any] = {"type": "object", "properties": props, "additionalProperties": True}
    if required:
        out["required"] = list(required)
    return out


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": values}


def nullable(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, {"type": "null"}]}


def or_spilled(schema: dict[str, Any]) -> dict[str, Any]:
    return {"anyOf": [schema, SPILLED]}


# ---- JSON conversion ----------------------------------------------------------------------


def jsonable(value: Any) -> Any:
    """``value`` as plain JSON: dates and times as ISO 8601, decimals as strings, bytes as
    base64, non-finite floats as null."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, decimal.Decimal):
        return str(value)
    if isinstance(value, bytes | bytearray):
        return base64.b64encode(bytes(value)).decode("ascii")
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [jsonable(v) for v in value]
    return str(value)


# ---- targets ------------------------------------------------------------------------------


def load_schema(args: dict[str, Any]) -> GenSchema:
    """The generation schema ``args`` name: a domain (with ``mode``) or a schema file, loaded by
    the code ``shape generate`` loads it with. ``profile`` is checked."""
    from shape.cli.generation import load_target

    target = str(args["domain"])
    schema = load_target(target, args.get("mode"))
    check_profile(target, args.get("profile"))
    return schema


def domain_profiles(name: str) -> list[str]:
    """The distribution profiles of an installed domain; ``default`` is always first."""
    from shape.generation.domains import load_domain

    return ["default", *sorted(load_domain(name).definition.profiles)]


def check_profile(target: str, profile: str | None) -> None:
    from shape.cli.generation import _is_file

    if profile is None or profile == "default":
        return
    if _is_file(target):
        raise BridgeError(
            "input.invalid_value", f"{target} is a schema file: it has no profile {profile!r}"
        )
    known = domain_profiles(target)
    if profile not in known:
        raise BridgeError(
            "input.invalid_value",
            f"domain {target!r} has no profile {profile!r} (it has: {', '.join(known)})",
        )
    raise BridgeError(
        "policy.capability_unavailable",
        f"profile {profile!r} is listed by {target!r} but applying a domain profile to "
        "generation is not supported yet",
        "use the default profile",
    )


def check_scale(schema: GenSchema, scale: str | None) -> None:
    from shape.cli.generation import _check_scale

    _check_scale(schema, scale)
