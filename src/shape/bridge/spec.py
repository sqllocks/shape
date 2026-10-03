"""Command specifications: what each command takes and returns (P6-11).

One :class:`Command` per bridge command. Its argument list drives three things from one source:
the checking of a request at run time, the published request schema, and the documentation.
The result schema is written by hand beside it, because a result is what Shape computes.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from shape.bridge.protocol import BridgeError

if TYPE_CHECKING:
    from shape.bridge.context import Context

Handler = Callable[[dict[str, Any], "Context"], dict[str, Any]]

_TYPES = ("string", "integer", "number", "boolean", "object", "array")


@dataclass(frozen=True)
class Arg:
    """One argument. ``items`` is the element type of an array."""

    type: str
    description: str
    required: bool = False
    default: Any = None
    enum: tuple[str, ...] | None = None
    minimum: int | float | None = None
    items: str | None = None
    secret: bool = False

    def __post_init__(self) -> None:
        if self.type not in _TYPES:
            raise AssertionError(f"unknown argument type {self.type!r}")

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type, "description": self.description}
        if self.enum is not None:
            out["enum"] = list(self.enum)
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.items is not None:
            out["items"] = {"type": self.items}
        if self.default is not None:
            out["default"] = self.default
        return out


@dataclass(frozen=True)
class Command:
    """A bridge command.

    ``job`` says it may run as a job (``options.async``); ``always_job`` that it always does (a
    stream); ``cancellable`` that a running job notices a cancel request; ``started`` shapes the
    immediate result of a job (default: the job itself); ``prepare`` checks a request before a job
    is made for it, so a bad request is refused and leaves no failed job behind. ``pending`` names
    what a command waits on (it answers ``policy.capability_unavailable`` until then).
    """

    name: str
    summary: str
    args: dict[str, Arg]
    result: dict[str, Any]
    handler: Handler
    job: bool = False
    always_job: bool = False
    cancellable: bool = False
    started: Callable[[dict[str, Any]], dict[str, Any]] | None = None
    prepare: Callable[[dict[str, Any], Context], None] | None = None
    pending: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def request_schema(self) -> dict[str, Any]:
        props = {k: a.schema() for k, a in self.args.items()}
        required = [k for k, a in self.args.items() if a.required]
        schema: dict[str, Any] = {
            "type": "object",
            "properties": props,
            "additionalProperties": False,
        }
        if required:
            schema["required"] = required
        return schema


def _type_ok(arg: Arg, value: Any) -> bool:
    if arg.type == "string":
        return isinstance(value, str)
    if arg.type == "boolean":
        return isinstance(value, bool)
    if arg.type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if arg.type == "number":
        return (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and math.isfinite(float(value))
        )
    if arg.type == "object":
        return isinstance(value, dict)
    return isinstance(value, list) and (
        arg.items is None or all(_type_ok(Arg(arg.items, ""), v) for v in value)
    )


def check_args(command: Command, args: dict[str, Any]) -> dict[str, Any]:
    """The arguments of a request, checked against the command: unknown names, missing required
    ones, wrong types and values outside ``enum`` or ``minimum`` are errors. ``null`` is the same
    as absent."""
    unknown = sorted(set(args) - set(command.args))
    if unknown:
        raise BridgeError(
            "usage.unknown_argument",
            f"{command.name} does not take argument(s): {', '.join(unknown)}",
            f"it takes: {', '.join(sorted(command.args)) or 'no arguments'}",
        )
    given = {k: v for k, v in args.items() if v is not None}
    for name, arg in command.args.items():
        if name not in given:
            if arg.required:
                raise BridgeError(
                    "usage.missing_argument", f"{command.name} needs the argument {name!r}"
                )
            continue
        value = given[name]
        if not _type_ok(arg, value):
            raise BridgeError(
                "usage.invalid_argument",
                f"argument {name!r} must be of type {arg.type}"
                + (f" of {arg.items}" if arg.items else "")
                + f", got {type(value).__name__}",
            )
        if arg.enum is not None and value not in arg.enum:
            raise BridgeError(
                "usage.invalid_argument",
                f"argument {name!r} must be one of {', '.join(arg.enum)}, got {value!r}",
            )
        if arg.minimum is not None and value < arg.minimum:
            raise BridgeError(
                "usage.invalid_argument",
                f"argument {name!r} must be at least {arg.minimum}, got {value!r}",
            )
        if arg.type == "string" and name in ("domain", "schema_path", "session_id") and not value:
            raise BridgeError("usage.invalid_argument", f"argument {name!r} must not be empty")
    return given


#: The options every request may carry (``options`` in the envelope).
OPTIONS: dict[str, Arg] = {
    "include_raw_values": Arg(
        "boolean",
        "return raw values (minimum, maximum, category values, offending values) even for "
        "classified columns; off by default",
        default=False,
    ),
    "async": Arg(
        "boolean",
        "for a command that can run as a job: return a job id at once and run in the background",
        default=False,
    ),
    "max_inline_bytes": Arg(
        "integer",
        "a result part larger than this is written to a file and returned as a reference",
        minimum=1024,
        default=262144,
    ),
}


def check_options(options: dict[str, Any]) -> dict[str, Any]:
    unknown = sorted(set(options) - set(OPTIONS))
    if unknown:
        raise BridgeError(
            "usage.unknown_argument",
            f"unknown option(s): {', '.join(unknown)}",
            f"the options are: {', '.join(sorted(OPTIONS))}",
        )
    for name, value in options.items():
        if value is None:
            continue
        arg = OPTIONS[name]
        if not _type_ok(arg, value) or (arg.minimum is not None and value < arg.minimum):
            raise BridgeError("usage.invalid_argument", f"option {name!r} is invalid: {value!r}")
    return {k: v for k, v in options.items() if v is not None}


def options_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {k: a.schema() for k, a in OPTIONS.items()},
        "additionalProperties": False,
    }
