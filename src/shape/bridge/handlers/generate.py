"""``generate`` and ``preview``: synthetic data from a domain or a generation schema. They run what
``shape generate`` runs (the engine and its writers). Everything they return is generated, not read
from real data."""

from __future__ import annotations

from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import writing
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    NUM,
    STR,
    STRS,
    arr,
    check_scale,
    jsonable,
    load_schema,
    mapping,
    obj,
    or_spilled,
)
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_FORMATS = ("summary", "csv", "tsv", "jsonl", "parquet", "excel", "sql", "delta")
_DOMAIN = Arg("string", "an installed domain (see `list`) or a generation schema file", True)
_MODE = Arg("string", "the schema mode of a domain", enum=("3nf", "star"))
_PROFILE = Arg("string", "a distribution profile of the domain (default: `default`)")
_SCALE = Arg("string", "a scale preset (see `describe`)")
_SEED = Arg("integer", "the seed (default: the schema's)")
MAX_PREVIEW_ROWS = 10_000


def cmd_generate(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.generation.engine import Engine
    from shape.generation.output import write_result

    schema = load_schema(args)
    scale = args.get("scale")
    check_scale(schema, scale)
    fmt = args.get("format", "summary")
    out_dir = args.get("output_dir")
    if fmt != "summary" and not out_dir:
        raise BridgeError(
            "usage.missing_argument",
            f"format {fmt!r} writes files: give output_dir",
            "or use format 'summary' to generate without writing",
        )
    if fmt == "summary" and out_dir:
        ctx.warn("output_dir_ignored", "format is 'summary': nothing is written")
    engine = Engine(schema, scale=scale, seed=args.get("seed"))
    result = engine.generate()
    problems = result.verify_integrity()
    tables = {
        name: {"rows": result.tables[name].num_rows, "columns": result.tables[name].num_columns}
        for name in result.generation_order
    }
    response: dict[str, Any] = {
        "domain": str(args["domain"]),
        "scale": engine.schema.generation.scale,
        "seed": engine.seed,
        "total_rows": sum(t["rows"] for t in tables.values()),
        "tables": tables,
        "integrity_errors": problems,
        "integrity_pass": not problems,
        "elapsed_seconds": round(result.elapsed_seconds, 3),
    }
    if fmt != "summary":
        with writing():
            paths = write_result(result, fmt, str(out_dir))
        response["output_format"] = fmt
        response["output_dir"] = str(out_dir)
        response["files"] = [str(p) for p in paths]
    return response


def cmd_preview(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.generation.engine import Engine

    schema = load_schema(args)
    rows = int(args.get("rows", 5))
    if rows > MAX_PREVIEW_ROWS:
        raise BridgeError(
            "usage.invalid_argument",
            f"rows is at most {MAX_PREVIEW_ROWS} for a preview, got {rows}",
            "use `generate` with a format to write the data to files",
        )
    wanted = args.get("tables")
    if wanted:
        unknown = [t for t in wanted if t not in schema.tables]
        if unknown:
            raise BridgeError(
                "input.invalid_value",
                f"no such table(s): {', '.join(unknown)} (the tables are: "
                f"{', '.join(schema.tables)})",
            )
    scale = "small" if "small" in schema.generation.scales else None
    result = Engine(schema, scale=scale, seed=args.get("seed")).generate()
    preview: dict[str, Any] = {}
    for name in result.generation_order:
        if wanted and name not in wanted:
            continue
        table = result.tables[name]
        data = [jsonable(r) for r in table.slice(0, rows).to_pylist()]
        preview[name] = {
            "total_rows": table.num_rows,
            "preview_rows": len(data),
            "columns": list(table.column_names),
            "data": ctx.spill(f"preview of {name}", data),
        }
    return {"domain": str(args["domain"]), "seed": result.schema.model.seed, "tables": preview}


COMMANDS = [
    Command(
        "generate",
        "Generate every table of a domain or schema; optionally write the files.",
        {
            "domain": _DOMAIN,
            "scale": _SCALE,
            "seed": _SEED,
            "format": Arg(
                "string", "summary (default: write nothing) or a file format", enum=_FORMATS
            ),
            "output_dir": Arg("string", "the directory to write the files to (needs a format)"),
            "mode": _MODE,
            "profile": _PROFILE,
        },
        obj(
            {
                "domain": STR,
                "scale": STR,
                "seed": INT,
                "total_rows": INT,
                "tables": mapping(obj({"rows": INT, "columns": INT})),
                "integrity_errors": STRS,
                "integrity_pass": BOOL,
            },
            {
                "elapsed_seconds": NUM,
                "output_format": STR,
                "output_dir": STR,
                "files": STRS,
            },
        ),
        cmd_generate,
        job=True,
    ),
    Command(
        "preview",
        "Generate a small sample and return its first rows as JSON.",
        {
            "domain": _DOMAIN,
            "rows": Arg("integer", "rows per table (default 5, at most 10000)", minimum=1),
            "seed": _SEED,
            "tables": Arg("array", "only these tables", items="string"),
            "mode": _MODE,
            "profile": _PROFILE,
        },
        obj(
            {
                "domain": STR,
                "seed": INT,
                "tables": mapping(
                    obj(
                        {
                            "total_rows": INT,
                            "preview_rows": INT,
                            "columns": STRS,
                            "data": or_spilled(arr(mapping(ANY))),
                        }
                    )
                ),
            }
        ),
        cmd_preview,
    ),
]
