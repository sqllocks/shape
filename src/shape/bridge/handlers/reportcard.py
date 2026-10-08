"""``report_card`` and ``report_card_read`` (bridge 1.2).

``report_card`` runs what ``shape report-card`` runs (``shape.quality.report_card``) and returns the
``shape-report-card`` document, which holds figures about the data (scores, rates, counts) and no
value from the real data. ``output`` also writes the card as JSON, as ``shape report-card -o
CARD.json`` does. ``report_card_read`` reads a stored card back after checking its ``format`` and
``version``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import writing
from shape.bridge.handlers.common import (
    ANY,
    INT,
    STR,
    STRS,
    mapping,
    obj,
    or_spilled,
)
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_SECTIONS = ("fidelity", "utility", "privacy")

_CARD = obj(
    {
        "format": {"const": "shape-report-card"},
        "version": INT,
        "inputs": mapping(ANY),
        "sections": mapping(ANY),
        "overall": {"enum": ["pass", "fail"]},
        "overall_reasons": STRS,
    },
    {"shape_version": STR, "require": STRS},
)


def prepare_report_card(args: dict[str, Any], ctx: Context) -> None:
    for name in ("real", "synthetic", "holdout", "manifest", "config"):
        value = args.get(name)
        if value is not None and not Path(str(value)).exists():
            raise BridgeError("input.not_found", f"{name} not found: {value}")


def cmd_report_card(args: dict[str, Any], ctx: Context) -> Any:
    from shape.quality.reportcard import report_card

    card = report_card(
        str(args["real"]),
        str(args["synthetic"]),
        config=args.get("config"),
        tiers=[int(t) for t in args["tiers"]] if args.get("tiers") else (1, 2),
        holdout=args.get("holdout"),
        manifest=args.get("manifest"),
        require=list(args.get("require") or ()),
    )
    data = card.to_dict()
    output = args.get("output")
    if output:
        with writing():
            Path(str(output)).write_text(
                json.dumps(data, indent=2, allow_nan=False) + "\n", "utf-8", newline="\n"
            )
    return ctx.spill("the report card", data)


def read_card(path: str) -> dict[str, Any]:
    """The report card in ``path``, checked: ``input.invalid_schema`` for a file that is not one,
    ``input.unsupported_format_version`` for one a newer Shape wrote."""
    from shape.quality.reportcard import FORMAT, VERSION, ReportCardError, parse_report_card

    text = Path(path).read_text(encoding="utf-8")  # a missing file is input.not_found
    try:
        doc = json.loads(text)
    except ValueError as exc:
        raise BridgeError("input.invalid_schema", f"{path}: not valid JSON: {exc}") from exc
    version = doc.get("version") if isinstance(doc, dict) else None
    if (
        isinstance(doc, dict)
        and doc.get("format") == FORMAT
        and isinstance(version, int)
        and not isinstance(version, bool)
        and version > VERSION
    ):
        raise BridgeError(
            "input.unsupported_format_version",
            f"{path} is a report card of version {version}, which is newer than the version "
            f"{VERSION} this Shape reads",
            "upgrade Shape to read it",
        )
    try:
        return parse_report_card(doc)
    except ReportCardError as exc:
        raise BridgeError("input.invalid_schema", f"{path}: {exc}") from exc


def cmd_report_card_read(args: dict[str, Any], ctx: Context) -> Any:
    return ctx.spill("the report card", read_card(str(args["path"])))


COMMANDS = [
    Command(
        "report_card",
        "One report card for a synthetic dataset: fidelity, utility and privacy (job-capable).",
        {
            "real": Arg(
                "string",
                "the real data: a data file, or a folder with one file per table",
                True,
                path="read",
            ),
            "synthetic": Arg(
                "string",
                "the synthetic data: a data file, or a folder with one file per table",
                True,
                path="read",
            ),
            "config": Arg(
                "string",
                "a shape-verify-config file (its `utility` section drives the utility gate)",
                path="read",
            ),
            "tiers": Arg(
                "array", "the fidelity tiers to run, of 1 and 2 (default 1 and 2)", items="integer"
            ),
            "holdout": Arg(
                "string",
                "real rows that were not given to the generator, as a data file or a folder with "
                "one file per table: turns on the membership-inference test",
                path="read",
            ),
            "manifest": Arg(
                "string",
                "the run manifest file of the generation: adds its reproducibility tuple and "
                "dataset id",
                path="read",
            ),
            "require": Arg(
                "array",
                "sections that must have run: one that was not run fails the card",
                items="string",
                items_enum=_SECTIONS,
            ),
            "output": Arg("string", "a file to also write the card to, as JSON", path="write"),
        },
        or_spilled(_CARD),
        cmd_report_card,
        job=True,
        prepare=prepare_report_card,
        since="1.2",
        effects=("reads_files", "writes_files"),
        notes=("a card with `overall` fail is a result, not an error",),
    ),
    Command(
        "report_card_read",
        "Read a stored report card after checking its format and version.",
        {
            "path": Arg(
                "string", "the report card file (format shape-report-card)", True, path="read"
            )
        },
        or_spilled(_CARD),
        cmd_report_card_read,
        since="1.2",
        effects=("reads_files",),
    ),
]
