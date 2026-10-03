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

from shape.bridge.protocol import API_MINOR, BridgeError

if TYPE_CHECKING:
    from shape.bridge.context import Context

Handler = Callable[[dict[str, Any], "Context"], dict[str, Any]]

_TYPES = ("string", "integer", "number", "boolean", "object", "array")

#: The versions a command or argument can have been added in, with their minor number.
SINCE = {"1.0": 0, "1.1": 1, "1.2": 2}
#: What a command can do to the system outside the bridge's own job files (published as
#: ``effects`` in ``index.json``).
EFFECTS = ("reads_files", "writes_files", "cancels", "network")
PATH_MODES = ("read", "write")


def minor_of(since: str) -> int:
    return SINCE[since]


@dataclass(frozen=True)
class Arg:
    """One argument. ``items`` is the element type of an array."""

    type: str
    description: str
    required: bool = False
    default: Any = None
    enum: tuple[str, ...] | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    items: str | None = None
    #: The values an element of an array may take (``enum`` is for a scalar).
    items_enum: tuple[str, ...] | None = None
    #: Values of ``enum`` or ``items_enum`` that a later version added (value to version): a
    #: request served as an older one does not know them, and gets the refusal it always got.
    enum_since: dict[str, str] | None = None
    secret: bool = False
    #: The version that added the argument; a request for an older one does not know it.
    since: str = "1.0"
    #: ``"read"`` or ``"write"`` when the argument is a filesystem path (a list of paths too).
    path: str | None = None
    #: A domain: an installed name first, a file path otherwise.
    name_or_path: bool = False

    def __post_init__(self) -> None:
        if self.type not in _TYPES:
            raise AssertionError(f"unknown argument type {self.type!r}")
        if self.since not in SINCE:
            raise AssertionError(f"unknown version {self.since!r}")
        if self.path is not None and self.path not in PATH_MODES:
            raise AssertionError(f"unknown path mode {self.path!r}")

    def schema(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "type": self.type,
            "description": self.description,
            "x-since": self.since,
        }
        if self.path is not None:
            out["x-path"] = self.path
        if self.name_or_path:
            out["x-name-or-path"] = True
        if self.enum is not None:
            out["enum"] = list(self.enum)
        if self.enum_since and self.items is None:
            out["x-enum-since"] = dict(sorted(self.enum_since.items()))
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.items is not None:
            out["items"] = {"type": self.items}
            if self.items_enum is not None:
                out["items"]["enum"] = list(self.items_enum)
            if self.enum_since:
                out["items"]["x-enum-since"] = dict(sorted(self.enum_since.items()))
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
    #: The version that added the command; a request for an older one gets ``unknown_command``.
    since: str = "1.0"
    #: What the command does outside the bridge: a subset of :data:`EFFECTS`.
    effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.since not in SINCE:
            raise AssertionError(f"unknown version {self.since!r}")
        bad = [e for e in self.effects if e not in EFFECTS]
        if bad:
            raise AssertionError(f"unknown effect {bad[0]!r}")

    def visible_args(self, minor: int) -> dict[str, Arg]:
        """The arguments a request served as ``1.<minor>`` knows."""
        return {k: a for k, a in self.args.items() if minor_of(a.since) <= minor}

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


def _enum_at(arg: Arg, values: tuple[str, ...], minor: int) -> tuple[str, ...]:
    """The values of an enumeration a request served as ``1.<minor>`` knows."""
    since = arg.enum_since or {}
    return tuple(v for v in values if minor_of(since.get(v, "1.0")) <= minor)


def check_args(command: Command, args: dict[str, Any], minor: int = API_MINOR) -> dict[str, Any]:
    """The arguments of a request, checked against the command: unknown names, missing required
    ones, wrong types and values outside ``enum`` or ``minimum`` are errors. ``null`` is the same
    as absent. A request served as an older ``minor`` does not know the arguments added later."""
    known = command.visible_args(minor)
    unknown = sorted(set(args) - set(known))
    if unknown:
        newer = sorted(n for n in unknown if n in command.args)
        since = f"1.{max(minor_of(command.args[n].since) for n in newer)}" if newer else ""
        raise BridgeError(
            "usage.unknown_argument",
            f"{command.name} does not take argument(s): {', '.join(unknown)}",
            f"it takes: {', '.join(sorted(known)) or 'no arguments'}"
            + (f"; {', '.join(newer)} needs api_version {since}" if newer else ""),
        )
    given = {k: v for k, v in args.items() if v is not None}
    for name, arg in known.items():
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
        if arg.enum is not None:
            allowed_enum = _enum_at(arg, arg.enum, minor)
            if value not in allowed_enum:
                raise BridgeError(
                    "usage.invalid_argument",
                    f"argument {name!r} must be one of {', '.join(allowed_enum)}, got {value!r}",
                )
        if arg.minimum is not None and value < arg.minimum:
            raise BridgeError(
                "usage.invalid_argument",
                f"argument {name!r} must be at least {arg.minimum}, got {value!r}",
            )
        if arg.maximum is not None and value > arg.maximum:
            raise BridgeError(
                "usage.invalid_argument",
                f"argument {name!r} must be at most {arg.maximum}, got {value!r}",
            )
        if arg.items_enum is not None:
            allowed = _enum_at(arg, arg.items_enum, minor)
            bad = [v for v in value if v not in allowed]
            if bad:
                raise BridgeError(
                    "usage.invalid_argument",
                    f"argument {name!r} may only hold {', '.join(allowed)}, got {bad[0]!r}",
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
