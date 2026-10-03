"""The bridge envelope: versions, requests, responses and the error codes (P6-11).

A request is one JSON object::

    {"api_version": "1.0", "id": "r1", "command": "generate", "args": {...}, "options": {...}}

and every request gets exactly one response object::

    {"api_version": "1.0", "id": "r1", "command": "generate", "ok": true, "result": {...},
     "warnings": [...]}
    {"api_version": "1.0", "id": "r1", "command": "generate", "ok": false,
     "error": {"code": "input.unknown_domain", "group": "input", "message": "...", "hint": "..."},
     "warnings": [...]}

Versioning: ``api_version`` is ``MAJOR.MINOR``. A minor version only ever adds (a command, an
optional argument, a result field); a major version may break. The server serves one major
version and every minor of it up to its own. Nothing here imports numpy, pyarrow or the engine
(T-18).

A request that declares an older minor is answered exactly as that minor answered: a command or
argument added later is unknown to it (``Request.minor`` says which minor a request is served as).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

API_MAJOR = 1
API_MINOR = 1
API_VERSION = f"{API_MAJOR}.{API_MINOR}"
SUPPORTED_RANGE = f"{API_MAJOR}.0 to {API_VERSION}"

MAX_REQUEST_BYTES = 8 * 1024 * 1024
MAX_ID_LENGTH = 128
DEFAULT_MAX_INLINE_BYTES = 256 * 1024

GROUPS = ("usage", "input", "policy", "privacy", "io", "auth", "internal")

#: Every error code the bridge returns, with what it means. A code is never renamed or removed
#: within a major version; new ones are added. The first part of a code is its group.
ERROR_CODES: dict[str, str] = {
    "usage.invalid_json": "the request is not valid JSON",
    "usage.invalid_request": "the request is not a well-formed envelope",
    "usage.request_too_large": "the request is larger than the bridge accepts",
    "usage.unsupported_version": "the request's api_version is not one this bridge serves",
    "usage.unknown_command": "no command has this name",
    "usage.missing_argument": "a required argument is missing",
    "usage.unknown_argument": "an argument or option the command does not take",
    "usage.invalid_argument": "an argument or option has the wrong type or an impossible value",
    "input.not_found": "a file or directory the request names does not exist",
    "input.unknown_domain": "no installed domain has this name",
    "input.invalid_schema": "a schema, profile, contract or data file is not valid",
    "input.invalid_value": "the data or settings given cannot be used",
    "input.unknown_job": "no job has this id",
    "input.job_state": "the job is not in a state the command needs",
    "input.job_interrupted": "the process that ran a job ended before the job finished",
    "input.unsupported_format_version": "a stored file was written by a newer Shape",
    "input.unknown_proposal": "the decision file has no proposal with this id",
    "input.unknown_source": "the project file has no source with this name",
    "input.unknown_format": "Shape has no published schema with this name",
    "policy.capability_unavailable": "the operation needs something that is not installed here",
    "policy.signature_invalid": "an artifact's signature does not verify",
    "policy.not_permitted": "a security or trust policy refuses the operation",
    "privacy.raw_values_withheld": "the operation would return raw values and was not asked to",
    "io.read_failed": "a file could not be read",
    "io.write_failed": "a file could not be written",
    "io.sink_failed": "a sink or remote service failed",
    "auth.missing_credentials": "a command needs a token or credential that was not given",
    "auth.rejected": "a remote service rejected the credential",
    "internal.error": "a bug in Shape: the message names the exception",
}

#: Every warning code a response carries in ``warnings``, with what it means. Like an error code, a
#: warning code is never renamed or removed within a major version.
WARNING_CODES: dict[str, str] = {
    "api_version_assumed": "the request declared no api_version: the bridge's own was assumed",
    "newer_minor_version": "the request declared a newer minor version than the bridge serves",
    "artifact_not_verified": "a .shape file was read and it is not signed",
    "empty_table": "a profiled table has 0 rows",
    "profile_file_holds_values": "the profile file written holds real values from the data",
    "domain_load_failed": "an installed domain did not load and is left out of the list",
    "output_dir_ignored": "output_dir was given but nothing is written for this format",
    "result_in_file": "a result part was larger than max_inline_bytes and is in a file",
    "project_source_not_selected": "the project file has several sources and none was selected",
}

_ID_TYPES = (str, int)
_KEYS = frozenset({"api_version", "id", "command", "args", "options"})
_VERSION = re.compile(r"^(0|[1-9][0-9]{0,3})\.(0|[1-9][0-9]{0,3})$")


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


class BridgeError(Exception):
    """A failure with a stable code. ``hint`` says what to do about it."""

    def __init__(self, code: str, message: str, hint: str | None = None) -> None:
        if code not in ERROR_CODES:
            raise AssertionError(f"unregistered bridge error code {code!r}")
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        #: Set when the request's id was readable, so the response can echo it.
        self.request_id: str | int | None = None

    @property
    def group(self) -> str:
        return group_of(self.code)

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "group": self.group, "message": self.message, "hint": self.hint}


def group_of(code: str) -> str:
    return code.split(".", 1)[0]


@dataclass
class Request:
    """A parsed request."""

    command: str
    args: dict[str, Any] = field(default_factory=dict)
    options: dict[str, Any] = field(default_factory=dict)
    id: str | int | None = None
    api_version: str | None = None
    warnings: list[dict[str, str]] = field(default_factory=list)
    #: The minor version the request is served as: its declared one, at most this bridge's.
    minor: int = API_MINOR


def warning(code: str, message: str) -> dict[str, str]:
    if code not in WARNING_CODES:
        raise AssertionError(f"unregistered bridge warning code {code!r}")
    return {"code": code, "message": message}


def parse_version(text: Any) -> tuple[int, int]:
    """``"1.0"`` to ``(1, 0)``; anything else is ``usage.invalid_request``."""
    if not isinstance(text, str) or not _VERSION.match(text):
        raise BridgeError(
            "usage.invalid_request",
            f"api_version must be a string like {API_VERSION!r}, got {text!r}",
        )
    major, minor = text.split(".")
    return int(major), int(minor)


def check_version(text: Any) -> list[dict[str, str]]:
    """Refuse a major version this bridge does not serve; warn about a newer minor."""
    major, minor = parse_version(text)
    if major != API_MAJOR:
        raise BridgeError(
            "usage.unsupported_version",
            f"api_version {text} is not supported: this bridge serves {SUPPORTED_RANGE}",
            f"send api_version {API_VERSION}",
        )
    if minor > API_MINOR:
        return [
            warning(
                "newer_minor_version",
                f"api_version {text} is newer than this bridge's {API_VERSION}: commands, "
                "arguments or fields added after it are not available",
            )
        ]
    return []


def parse_request(raw: str | bytes | Any) -> Request:
    """Parse one request from its JSON text (or an already-decoded value)."""
    if isinstance(raw, str | bytes):
        if len(raw) > MAX_REQUEST_BYTES:
            raise BridgeError(
                "usage.request_too_large",
                f"a request may be at most {MAX_REQUEST_BYTES} bytes",
                "pass large inputs as file paths",
            )
        try:
            doc = json.loads(raw, parse_constant=_reject_constant)
        except (ValueError, RecursionError) as exc:
            raise BridgeError("usage.invalid_json", f"not valid JSON: {exc}") from exc
    else:
        doc = raw
    if not isinstance(doc, dict):
        raise BridgeError("usage.invalid_request", "a request is a JSON object")
    request_id = doc.get("id")
    if request_id is not None and (
        not isinstance(request_id, _ID_TYPES)
        or isinstance(request_id, bool)
        or len(str(request_id)) > MAX_ID_LENGTH
    ):
        raise BridgeError(
            "usage.invalid_request",
            f"id must be a string or an integer of at most {MAX_ID_LENGTH} characters",
        )
    unknown = sorted(set(doc) - _KEYS)
    if unknown:
        raise _with_id(
            BridgeError(
                "usage.invalid_request",
                f"unknown request field(s): {', '.join(unknown)}; "
                f"a request has: {', '.join(sorted(_KEYS))}",
            ),
            request_id,
        )
    warnings: list[dict[str, str]] = []
    version = doc.get("api_version")
    minor = API_MINOR
    try:
        if version is None:
            warnings.append(
                warning(
                    "api_version_assumed", f"no api_version in the request: assumed {API_VERSION}"
                )
            )
        else:
            warnings.extend(check_version(version))
            minor = min(parse_version(version)[1], API_MINOR)
        command = doc.get("command")
        if not isinstance(command, str) or not command:
            raise BridgeError("usage.invalid_request", "command must be a non-empty string")
        args = doc.get("args", {})
        options = doc.get("options", {})
        if args is None:
            args = {}
        if options is None:
            options = {}
        if not isinstance(args, dict):
            raise BridgeError("usage.invalid_request", "args must be an object")
        if not isinstance(options, dict):
            raise BridgeError("usage.invalid_request", "options must be an object")
    except BridgeError as exc:
        raise _with_id(exc, request_id) from None
    return Request(command, args, options, request_id, version, warnings, minor)


def _with_id(exc: BridgeError, request_id: str | int | None) -> BridgeError:
    exc.request_id = request_id
    return exc


def ok_response(
    request: Request, result: Any, warnings: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "id": request.id,
        "command": request.command,
        "ok": True,
        "result": result,
        "warnings": [*request.warnings, *(warnings or [])],
    }


def error_response(
    error: BridgeError,
    *,
    request_id: str | int | None = None,
    command: str | None = None,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    return {
        "api_version": API_VERSION,
        "id": request_id,
        "command": command,
        "ok": False,
        "error": error.to_dict(),
        "warnings": list(warnings or []),
    }


def dumps(response: dict[str, Any]) -> str:
    """One response as one line of JSON (never a newline inside)."""
    return json.dumps(response, sort_keys=True, default=str, allow_nan=False, ensure_ascii=True)
