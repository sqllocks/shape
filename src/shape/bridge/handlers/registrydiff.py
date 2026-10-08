"""``registry_diff`` (bridge 1.2): drift between two versions of a name in a registry.

It calls what ``shape registry ROOT diff NAME REF1 REF2`` calls (``shape.registry.drift``) and
returns the shape of ``diff``: ``drifted``, ``change_count`` and ``changes`` (the change records of
``shape diff``: ``kind``, ``severity``, ``score``, the two values), spillable. Two share-safe
versions are compared on every metric both hold, and ``not_measured`` lists the metrics a safe form
withholds (the range of a column, for example). Two raw versions are compared as ``diff``
compares them, and the values of a classified column are withheld from the changes as ``diff``
withholds them. Two other kinds of document (or one of each) are refused: the command compares
profiles, and never returns a value of the data on its own.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers import flow
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    STR,
    arr,
    jsonable,
    nullable,
    obj,
    or_spilled,
)
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_CHANGE = obj(
    {"column": nullable(STR), "kind": STR, "severity": {"enum": ["low", "medium", "high"]}},
    {"baseline": ANY, "current": ANY, "score": ANY, "redacted": BOOL},
)
_NOT_MEASURED = obj({"column": STR, "metric": STR, "reason": STR}, {"table": nullable(STR)})


def _open(args: dict[str, Any]) -> Any:
    from shape.registry import LocalRegistry

    root = Path(str(args["root"]))
    if not root.exists():
        raise BridgeError("input.not_found", f"registry not found: {root}")
    if not (root / "logs").is_dir():  # opening it would create one
        raise BridgeError("input.invalid_value", f"{root} is not a registry: it has no logs folder")
    return LocalRegistry(root)


def _options(args: dict[str, Any]) -> dict[str, Any]:
    policy = args.get("policy")
    if policy is not None and not Path(str(policy)).is_file():
        raise BridgeError("input.not_found", f"policy not found: {policy}")
    return {"thresholds": args.get("thresholds") or None, "policy": policy}


def _raw_profile(blob: bytes) -> Any:
    """The profile in a registry version. The registry addresses its content by sha256, so a
    missing signature is not worth a warning that would name a scratch file."""
    import warnings

    import shape
    from shape.artifact.io import ArtifactNotVerifiedWarning

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "version.shape"
        path.write_bytes(blob)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ArtifactNotVerifiedWarning)
            return shape.load(str(path))


def cmd_registry_diff(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    import shape
    from shape.registry.drift import diff_safe, is_safe_profile
    from shape.registry.local import is_raw_profile

    registry = _open(args)
    options = _options(args)
    name = str(args["name"])
    id1, id2 = registry.resolve(name, str(args["ref1"])), registry.resolve(name, str(args["ref2"]))
    out: dict[str, Any] = {"name": name, "from": id1, "to": id2, "same": id1 == id2}
    if id1 == id2:
        return {
            **out,
            "form": None,
            "drifted": False,
            "change_count": 0,
            "changes": [],
            "not_measured": [],
        }
    first, second = registry.checkout(name, id1), registry.checkout(name, id2)
    if is_raw_profile(first) and is_raw_profile(second) and first[:2] == b"PK":
        before, after = _raw_profile(first), _raw_profile(second)
        result = shape.diff(before, after, **options)
        changes = jsonable(result.to_dict())["changes"]
        if not ctx.include_raw:
            classified = flow.classified_columns(before) | flow.classified_columns(after)
            changes = flow.redact_entries(changes, classified)
        form, not_measured = "raw", []
        drifted = result.drifted
    else:
        try:
            docs = [json.loads(blob) for blob in (first, second)]
        except ValueError:
            docs = []
        if len(docs) != 2 or not all(is_safe_profile(d) for d in docs):
            raise BridgeError(
                "input.invalid_value",
                f"{name}@{id1[:12]} and {name}@{id2[:12]} are not two profiles of the same form: "
                "registry_diff compares two raw profiles or two share-safe profiles",
                "use `diff` on two profile files",
            )
        found = diff_safe(docs[0], docs[1], **options)
        changes, not_measured, drifted = (
            jsonable(found["changes"]),
            found["not_measured"],
            found["drifted"],
        )
        form = "safe"
    return {
        **out,
        "form": form,
        "drifted": bool(drifted),
        "change_count": len(changes),
        "changes": ctx.spill("the changes", changes),
        "not_measured": not_measured,
    }


COMMANDS = [
    Command(
        "registry_diff",
        "Drift between two versions of a name in a registry: two raw profiles, or two "
        "share-safe profiles (with the metrics a safe form withholds listed apart).",
        {
            "root": Arg("string", "the registry folder", True, path="read"),
            "name": Arg("string", "the committed name", True),
            "ref1": Arg("string", "the first version: a ref, tag or content id", True),
            "ref2": Arg("string", "the second version", True),
            "policy": Arg(
                "string",
                "a drift policy file: thresholds, per-column thresholds, ignore and only lists "
                "(a contract's `drift` section works)",
                path="read",
            ),
            "thresholds": Arg("object", "drift thresholds that override the defaults"),
        },
        obj(
            {
                "name": STR,
                "from": STR,
                "to": STR,
                "same": BOOL,
                "form": nullable({"enum": ["raw", "safe"]}),
                "drifted": BOOL,
                "change_count": INT,
                "changes": or_spilled(arr(_CHANGE)),
                "not_measured": arr(_NOT_MEASURED),
            }
        ),
        cmd_registry_diff,
        since="1.2",
        effects=("reads_files",),
    )
]
