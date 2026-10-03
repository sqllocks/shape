"""``chaos`` (bridge 1.2): corrupt tables on purpose and write the ground-truth log.

It runs what ``shape chaos`` runs (``shape.chaos``) with the same input check (W1-17): an input
folder whose tables are not marked as Shape-generated answers ``policy.unverified_input`` before
anything is written, and ``allow_real_input`` runs it anyway with the warning
``real_input_corrupted`` (the ground-truth log then records ``input_provenance: unverified``).
Chaos writes locally only: an ``output_dir`` or ``ground_truth`` that is a URL answers
``policy.not_permitted``. A cancel is noticed before the files are written, so a cancelled job
writes nothing.

The result lists the files and the ground-truth log path, as ``shape chaos --json`` prints them. The
log holds the cells that were changed (before and after); it is written where the request says,
never returned.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import writing
from shape.bridge.handlers.common import ANY, INT, STR, STRS, arr, obj
from shape.bridge.jobs import JobCancelled
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_FORMATS = ("csv", "parquet", "jsonl")
_URL = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")


def not_local(value: Any, what: str) -> None:
    """``policy.not_permitted`` for a destination that is not on this machine's file system."""
    if value is not None and _URL.match(str(value)):
        raise BridgeError(
            "policy.not_permitted",
            f"{what} {value} is not a local path: chaos writes local files only",
            "give a folder on this machine",
        )


def prepare_chaos(args: dict[str, Any], ctx: Context) -> None:
    not_local(args.get("output_dir"), "output_dir")
    not_local(args.get("ground_truth"), "ground_truth")
    not_local(args.get("input"), "input")
    if not args.get("input") and not args.get("domain"):
        raise BridgeError(
            "input.invalid_value",
            "name a domain or schema file to generate, or give input (a folder of tables)",
        )
    if args.get("input") and not Path(str(args["input"])).is_dir():
        raise BridgeError("input.not_found", f"input folder not found: {args['input']}")


def _batch(args: dict[str, Any]) -> int:
    """The batch number, as ``shape chaos`` derives it. ``start_date`` derives it from a batch
    date, which the bridge does not take (a landing option), so it is refused as the command line
    refuses it without one."""
    if args.get("batch") is not None:
        if args.get("start_date"):
            raise BridgeError(
                "input.invalid_value", "give batch or start_date (with a batch date), not both"
            )
        return int(args["batch"])
    if args.get("start_date"):
        raise BridgeError(
            "input.invalid_value",
            "start_date needs a batch date, which the bridge does not take: give batch",
        )
    return 0


def _stop_if_cancelled(ctx: Context, stage: str) -> None:
    if ctx.cancel.is_set():
        raise JobCancelled({"stage": stage, "files": 0})


def cmd_chaos(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.chaos.groundtruth import corrupt_tables, parse_corruptions, write_ground_truth
    from shape.cli.chaos import _schema_maps
    from shape.cli.generation import _check_scale, load_target
    from shape.cli.incremental import read_tables, write_tables

    prepare_chaos(args, ctx)
    corruptions = parse_corruptions(list(args.get("corrupt") or []))
    batch = _batch(args)
    keys: dict[str, str] = {}
    refs: dict[str, str] = {}
    domain = args.get("domain")
    schema = load_target(str(domain), args.get("mode")) if domain else None
    if schema is not None:
        keys, refs = _schema_maps(schema)
    seed = (
        int(args["seed"])
        if args.get("seed") is not None
        else (schema.model.seed if schema is not None else 42)
    )
    output = Path(str(args["output_dir"]))
    provenance: str | None = None
    ctx.progress({"stage": "reading" if args.get("input") else "generating"})
    if args.get("input"):
        from shape.chaos.input_check import (
            ChaosInputError,
            check_output_folder,
            verify_chaos_input,
        )

        try:
            check_output_folder(args["input"], output)  # before anything is read or written
        except ChaosInputError as exc:
            raise BridgeError("input.invalid_value", str(exc)) from exc
        try:
            provenance = verify_chaos_input(
                args["input"], allow_real_input=bool(args.get("allow_real_input"))
            )
        except ChaosInputError as exc:
            raise BridgeError(
                "policy.unverified_input",
                str(exc),
                "chaos only corrupts data marked as Shape-generated; allow_real_input runs it "
                "anyway",
            ) from exc
        if provenance == "unverified":
            ctx.warn(
                "real_input_corrupted",
                "allow_real_input: corrupting data that is not marked as Shape-generated; the "
                "ground-truth log records input_provenance: unverified",
            )
        tables = read_tables(args["input"])
    else:
        from shape.generation.engine import Engine

        assert schema is not None
        _check_scale(schema, args.get("scale"))
        _stop_if_cancelled(ctx, "generating")
        tables = dict(Engine(schema, scale=args.get("scale"), seed=seed).generate().tables)
    _stop_if_cancelled(ctx, "corrupting")
    ctx.progress({"stage": "corrupting"})
    outcome = corrupt_tables(
        tables, corruptions, seed=seed, batch=batch, keys=keys, references=refs
    )
    outcome.input_provenance = provenance
    _stop_if_cancelled(ctx, "writing")  # nothing is written before this point
    ctx.progress({"stage": "writing"})
    log = Path(str(args["ground_truth"])) if args.get("ground_truth") else None
    with writing():
        files = write_tables(outcome.tables, str(args.get("format", "csv")), output)
        log = log or output / "_chaos_ground_truth.jsonl"
        write_ground_truth(log, outcome)
        from shape.io.provenance import record_tables

        record_tables(
            output,
            [*files, *([log] if log.parent.resolve() == output.resolve() else [])],
            {n: t.num_rows for n, t in outcome.tables.items()},
            seed=seed,
            domain=schema.model.domain or schema.model.name if schema is not None else None,
        )
    return {
        "output": str(output),
        "ground_truth": str(log),
        "seed": seed,
        "batch": batch,
        "files": [str(p) for p in files],
        "changes": len(outcome.records),
        "applied": outcome.applied,
    }


COMMANDS = [
    Command(
        "chaos",
        "Corrupt tables on purpose and write the ground-truth log (job-capable, cancellable "
        "before the files are written).",
        {
            "output_dir": Arg("string", "the folder the corrupted files go to", True, path="write"),
            "input": Arg(
                "string",
                "a folder of the tables to corrupt, one file per table (marked as "
                "Shape-generated, unless allow_real_input)",
                path="read",
            ),
            "domain": Arg(
                "string",
                "an installed domain or a generation schema file: it names the keys and foreign "
                "keys, and without `input` the tables are generated from it",
                name_or_path=True,
            ),
            "mode": Arg("string", "the schema mode of a domain", enum=("3nf", "star")),
            "scale": Arg("string", "the scale preset, when generating"),
            "seed": Arg("integer", "the seed (default: the schema's, else 42)"),
            "format": Arg("string", "the file format (default csv)", enum=_FORMATS),
            "corrupt": Arg(
                "array",
                "the corruptions, each as `shape chaos --corrupt` takes it: "
                "`KIND[=RATE][@TABLE[.COLUMN]][:OPT=V,...]`",
                True,
                items="string",
            ),
            "batch": Arg("integer", "the batch number (default 0)", minimum=0),
            "start_date": Arg(
                "string",
                "the date of batch 0 (needs a batch date, which the bridge does not take: "
                "give `batch`)",
            ),
            "ground_truth": Arg(
                "string",
                "the ground-truth log file (default: output_dir/_chaos_ground_truth.jsonl)",
                path="write",
            ),
            "allow_real_input": Arg(
                "boolean",
                "corrupt `input` tables that are not marked as Shape-generated: adds the warning "
                "real_input_corrupted and records input_provenance: unverified in the log",
            ),
        },
        obj(
            {
                "output": STR,
                "ground_truth": STR,
                "seed": INT,
                "batch": INT,
                "files": STRS,
                "changes": INT,
                "applied": arr(obj({"kind": STR, "table": STR, "rows": INT}, {"column": ANY})),
            }
        ),
        cmd_chaos,
        job=True,
        cancellable=True,
        prepare=prepare_chaos,
        since="1.2",
        effects=("reads_files", "writes_files", "cancels"),
    )
]
