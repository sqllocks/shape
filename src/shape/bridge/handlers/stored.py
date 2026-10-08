"""``profile_show``, ``contract_validate`` and ``safe_scan``: stored artifacts, read without
profiling anything again (bridge 1.1).

* ``profile_show`` reads a ``.shape`` profile that is already on disk and returns what ``profile``
  returns, with the same spilling and the same protection of classified columns;
* ``contract_validate`` checks the contract format ``shape check`` reads (``row_count``,
  ``columns``, ...) with the code ``shape check`` validates it with. The 1.0 ``validate`` command
  checks another kind of contract (``name`` and ``fields``) and is not changed;
* ``safe_scan`` runs the leak scanner of ``shape.privacy.safe_validator`` as
  ``shape profile validate --safe`` does. A finding names the rule and where it is; its message
  never holds the value it found unless the request sets ``options.include_raw_values``.
"""

from __future__ import annotations

import json
import re
import warnings
import zipfile
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    STR,
    arr,
    jsonable,
    mapping,
    nullable,
    obj,
    or_spilled,
)
from shape.bridge.handlers.flow import _load_profile, classified_columns, redact_summary
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

# ---- profile_show -------------------------------------------------------------------------


def cmd_profile_show(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.artifact.io import ArtifactNotVerifiedWarning, read_artifact
    from shape.profile.reference.profile import ARTIFACT_FORMAT, ARTIFACT_FORMAT_VERSION, load

    path = str(args["path"])
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        read = read_artifact(path)
        manifest = read[0]
        version = manifest.get("format_version")
        if (
            manifest.get("format") == ARTIFACT_FORMAT
            and isinstance(version, int)
            and not isinstance(version, bool)
            and version > ARTIFACT_FORMAT_VERSION
        ):
            raise BridgeError(
                "input.unsupported_format_version",
                f"{path} is a profile of format version {version}, which is newer than the "
                f"version {ARTIFACT_FORMAT_VERSION} this Shape reads",
                "upgrade Shape to read it",
            )
        prof = load(path)
    unverified = [w for w in caught if issubclass(w.category, ArtifactNotVerifiedWarning)]
    if unverified:
        ctx.warn("artifact_not_verified", str(unverified[0].message))
    for item in caught:
        if not issubclass(item.category, ArtifactNotVerifiedWarning):
            warnings.showwarning(item.message, item.category, item.filename, item.lineno)
    summary = prof.summary()
    if not ctx.include_raw:
        summary = redact_summary(summary, classified_columns(prof))
    result: dict[str, Any] = {
        "content_id": str(manifest["shape_content_id"]),
        "name": prof.name,
        "tables": {
            name: {"rows": t["row_count"], "columns": len(t["columns"])}
            for name, t in prof.tables.items()
        },
        "summary": ctx.spill("the profile summary", jsonable(summary)),
        "signed": read.signature["status"] != "unsigned",
    }
    if prof.provenance is not None:
        result["provenance"] = jsonable(prof.provenance)
    return result


# ---- contract_validate --------------------------------------------------------------------


def _exactly_one(args: dict[str, Any]) -> None:
    if ("text" in args) == ("path" in args):
        raise BridgeError("usage.invalid_argument", "give exactly one of text and path")


def contract_errors(doc: Any) -> list[dict[str, str]]:
    """Every problem of a contract of the format ``shape check`` reads, each with the
    ``location`` of the part it is about (``columns.NAME``, ``row_count``, ...; ``""`` for the
    contract as a whole). Empty when the contract is valid."""
    from shape.contracts.v1 import _CONTRACT_KEYS, ContractError, _validate_contract

    if not isinstance(doc, dict):
        return [{"location": "", "message": "a contract must be a JSON object"}]
    try:
        _validate_contract(doc)
        return []
    except ContractError as whole:
        first = str(whole)
    errors: list[dict[str, str]] = []

    def attempt(location: str, piece: dict[str, Any]) -> None:
        try:
            _validate_contract(piece)
        except ContractError as exc:
            errors.append({"location": location, "message": str(exc)})

    unknown = sorted(set(doc) - _CONTRACT_KEYS)
    if unknown:
        errors.append({"location": "", "message": f"unknown contract keys: {unknown}"})
    for key, value in doc.items():
        if key in unknown:
            continue
        if key == "columns" and isinstance(value, dict):
            for name, rules in value.items():
                attempt(f"columns.{name}", {"columns": {name: rules}})
        else:
            attempt(key, {key: value})
    return errors or [{"location": "", "message": first}]


def cmd_contract_validate(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    _exactly_one(args)
    if "path" in args:
        text = Path(str(args["path"])).read_text(encoding="utf-8")
    else:
        text = str(args["text"])
    try:
        doc = json.loads(text)
    except ValueError as exc:
        return {
            "valid": False,
            "kind": "check-contract",
            "errors": [{"location": "", "message": f"not valid JSON: {exc}"}],
            "warnings": [],
        }
    errors = contract_errors(doc)
    return {"valid": not errors, "kind": "check-contract", "errors": errors, "warnings": []}


# ---- safe_scan ----------------------------------------------------------------------------

_PII_LABEL = re.compile(r"^value matches the (\w+) pattern")
_REDACTED = "<redacted>"


def _message(rule: str, detail: str) -> str:
    """What a finding says without the value it found."""
    if rule == "pii-regex":
        found = _PII_LABEL.match(detail)
        return f"a value matches the {found[1]} pattern" if found else "a value matches a pattern"
    if rule == "extreme-pair":
        return "a numeric minimum and maximum pair: a raw minimum and maximum can identify a record"
    if rule == "raw-string-list":
        return "a list of raw strings: a value dump"
    if rule == "row-count-missing":
        return "row_count is absent, unknown or not a positive integer: node safety undeterminable"
    return detail


def _pointer(pointer: str) -> str:
    """``pointer`` with any personal-data value in it withheld (a key of a dictionary can be a
    value, and an email address holds dots, so the whole pointer is searched, not its parts)."""
    from shape.privacy.safe_validator import _PII_REGEXES

    for rx in _PII_REGEXES.values():
        pointer = rx.sub(_REDACTED, pointer)
    return pointer


def cmd_safe_scan(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.privacy.safe_validator import SafeProfileValidator, ValidationResult

    _exactly_one(args)
    validator = SafeProfileValidator()
    if "text" in args:
        label = "<text>"
        try:
            doc = json.loads(str(args["text"]))
        except (ValueError, RecursionError) as exc:
            result = ValidationResult(path=label)
            result.add("malformed", "$", f"not valid JSON: {exc}")
        else:
            result = validator.validate_data(doc, path=label)
    else:
        path = str(args["path"])
        if zipfile.is_zipfile(path):
            from shape.privacy.cli import scanned_document

            # the document `shape profile validate --safe` scans (W1-11: with its capture)
            result = validator.validate_data(scanned_document(_load_profile(path, ctx)), path=path)
        else:
            Path(path).stat()  # a missing file is input.not_found, not a finding
            result = validator.validate_file(path)
    findings = [
        {
            "rule": f.rule,
            "pointer": f.path if ctx.include_raw else _pointer(f.path),
            "message": f.detail if ctx.include_raw else _message(f.rule, f.detail),
        }
        for f in result.findings
    ]
    return {"clean": result.is_clean, "findings": findings}


# ---- the table ----------------------------------------------------------------------------

COMMANDS = [
    Command(
        "profile_show",
        "Show a stored .shape profile: its tables and summary, without profiling again.",
        {"path": Arg("string", "a .shape profile", True, path="read")},
        obj(
            {
                "content_id": STR,
                "name": nullable(STR),
                "tables": mapping(
                    obj({"rows": {"type": "integer"}, "columns": {"type": "integer"}})
                ),
                "summary": or_spilled(mapping(ANY)),
                "signed": BOOL,
            },
            {"provenance": mapping(ANY)},
        ),
        cmd_profile_show,
        since="1.1",
        effects=("reads_files",),
    ),
    Command(
        "contract_validate",
        "Validate a contract of the format `shape check` reads (row_count, columns, ...).",
        {
            "path": Arg("string", "a contract JSON file (give this or text)", path="read"),
            "text": Arg("string", "the contract as JSON text (give this or path)"),
        },
        obj(
            {
                "valid": BOOL,
                "errors": arr(obj({"location": STR, "message": STR})),
            },
            {"kind": STR, "warnings": arr(obj({"location": STR, "message": STR}))},
        ),
        cmd_contract_validate,
        since="1.1",
        effects=("reads_files",),
    ),
    Command(
        "safe_scan",
        "Scan a share-safe profile for leaks of values, as `shape profile validate --safe` does.",
        {
            "path": Arg(
                "string",
                "a share-safe profile JSON file, or a .shape file (give this or text)",
                path="read",
            ),
            "text": Arg("string", "the share-safe profile as JSON text (give this or path)"),
        },
        obj(
            {
                "clean": BOOL,
                "findings": arr(obj({"rule": STR, "pointer": STR, "message": STR})),
            }
        ),
        cmd_safe_scan,
        since="1.1",
        effects=("reads_files",),
    ),
]
