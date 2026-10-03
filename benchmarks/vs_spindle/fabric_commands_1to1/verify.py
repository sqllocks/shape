"""Shape's Fabric commands against the pinned baseline's, command by command (P6-07c).

    source scripts/env.sh && "$SHAPE_PY" benchmarks/vs_spindle/fabric_commands_1to1/verify.py \\
        [--negative-control]

Runs in the Shape venv; the baseline is driven through its own command line (and, for the
requests it sends, through ``baseline_rest.py`` in the baseline venv, with a fake Fabric service).

What is compared, and how:

* ``export-model``: the ``.bim`` document the command writes, for every option combination
  (``-s``, ``-o``, ``--source-type``, ``--source-name``, ``--include-measures/--no-measures``,
  ``--schema-name``), field by field, and the command's own report; and the library on
  synthetic schemas that use every type, key and relationship kind.
* ``notebook``: the cells (count, kinds, metadata) and the command's report, for each target.
* ``deploy-notebook`` and ``setup-fabric``: the requests each sends to a fake Fabric service
  (method, path, body), and the report.
* ``publish``: the landing zone it writes (files, row counts, columns) for each format, the run
  manifest (every key, every table's rows and columns, the run id's form), and the report.

Everything that may differ is in ``differences.py``, each entry shown by a probe below: anything
else that differs fails the run. ``--negative-control`` tampers with Shape's output and requests
and requires each tampering to be flagged. Exit codes: 0 every check holds (and every control
was flagged), 1 a check failed, 2 a run could not be produced.
"""

from __future__ import annotations

import argparse
import base64
import copy
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from differences import ALLOWED, brand, unversion  # noqa: E402
from paths import SHAPE_VENV, SPINDLE_ROOT, SPINDLE_VENV  # noqa: E402

SPINDLE_PY = SPINDLE_VENV / "bin" / "python"
SPINDLE_CLI = SPINDLE_VENV / "bin" / "spindle"
SHAPE_CLI = SHAPE_VENV / "bin" / "shape"
WORKSPACE = "11111111-1111-4111-8111-111111111111"
Problems = list[str]


class RunError(RuntimeError):
    """A run that could not be produced (exit 2 of this script)."""


# ---------------------------------------------------------------------------------------------
# running the tools


def run(cmd: list[str], cwd: Path, env: dict[str, str] | None = None) -> tuple[int, str, str]:
    done = subprocess.run(  # noqa: S603
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
        timeout=900,
        check=False,
    )
    return done.returncode, done.stdout, done.stderr


def baseline(args: list[str], cwd: Path) -> tuple[int, str, str]:
    return run([str(SPINDLE_CLI), *args], cwd)


def shape(args: list[str], cwd: Path) -> tuple[int, str, str]:
    return run([str(SHAPE_CLI), *args], cwd)


def baseline_json(script: str, args: list[str], env: dict[str, str] | None = None) -> Any:
    code, out, err = run([str(SPINDLE_PY), str(HERE / script), *args], HERE, env)
    if code != 0:
        raise RunError(f"{script} failed: {err[-400:]}")
    return json.loads(out.strip().splitlines()[-1])


# ---------------------------------------------------------------------------------------------
# comparing


def diff(a: Any, b: Any, path: str = "") -> Problems:
    """Where two JSON values differ: one line each."""
    if type(a) is not type(b):
        return [f"{path or '.'}: {a!r} != {b!r}"]
    if isinstance(a, dict):
        out: Problems = []
        for key in sorted(set(a) | set(b)):
            if key not in a or key not in b:
                out.append(f"{path}.{key}: only in {'shape' if key in b else 'baseline'}")
            else:
                out += diff(a[key], b[key], f"{path}.{key}")
        return out
    if isinstance(a, list):
        if len(a) != len(b):
            return [f"{path or '.'}: {len(a)} items != {len(b)}"]
        return [
            p for i, (x, y) in enumerate(zip(a, b, strict=True)) for p in diff(x, y, f"{path}[{i}]")
        ]
    return [] if a == b else [f"{path or '.'}: {a!r} != {b!r}"]


def compare_bim(base: Any, mine: Any) -> Problems:
    """Equal except the model name and the generated_by annotation, which must be Shape's."""
    base, mine = copy.deepcopy(base), copy.deepcopy(mine)
    problems: Problems = []
    if not re.fullmatch(r"Spindle[A-Z]\w*", base["name"]):
        problems.append(f"baseline model name {base['name']!r} is not the expected form")
    base["name"] = brand(base["name"])
    base["model"]["annotations"] = [
        {**a, "value": brand(a["value"])} for a in base["model"]["annotations"]
    ]
    for doc in (base, mine):
        for annotation in doc["model"]["annotations"]:
            if annotation["name"] == "generated_by":
                annotation["value"] = unversion(annotation["value"])
    return problems + diff(base, mine)


def lines(text: str) -> list[str]:
    return [ln.rstrip() for ln in text.splitlines()]


def compare_text(base: str, mine: str, *, drop: tuple[str, ...] = ()) -> Problems:
    a = [unversion(brand(ln)) for ln in lines(base) if not ln.lstrip().startswith(drop)]
    b = [unversion(ln) for ln in lines(mine) if not ln.lstrip().startswith(drop)]
    return diff(a, b)


# ---------------------------------------------------------------------------------------------
# export-model

EXPORT_CASES: list[list[str]] = [
    [],
    ["--source-type", "warehouse", "--source-name", "WH"],
    ["--source-type", "sql_database", "--source-name", "DB", "--schema-name", "sales"],
    ["--source-type", "lakehouse", "--source-name", "LH", "--no-measures"],
    ["-s", "medium", "--include-measures"],
    ["--source-type", "warehouse", "--no-measures", "--schema-name", "x"],
]


def check_export_cli(tmp: Path) -> Problems:
    problems: Problems = []
    for i, extra in enumerate(EXPORT_CASES):
        label = " ".join(extra) or "defaults"
        outs = {}
        for tool, call in (("baseline", baseline), ("shape", shape)):
            cwd = tmp / f"export{i}-{tool}"
            cwd.mkdir(parents=True)
            code, out, err = call(["export-model", "retail", "-o", "model.bim", *extra], cwd)
            if code != 0:
                raise RunError(f"{tool} export-model {label} exited {code}: {err[-300:]}")
            outs[tool] = (out, json.loads((cwd / "model.bim").read_text(encoding="utf-8")))
        problems += [f"[{label}] {p}" for p in compare_bim(outs["baseline"][1], outs["shape"][1])]
        problems += [
            f"[{label}] report {p}" for p in compare_text(outs["baseline"][0], outs["shape"][0])
        ]
    # the default output path of both tools
    for tool, call in (("baseline", baseline), ("shape", shape)):
        cwd = tmp / f"export-default-{tool}"
        cwd.mkdir()
        call(["export-model", "retail"], cwd)
        if not (cwd / "model.bim").is_file():
            problems.append(f"{tool} export-model with no -o did not write model.bim")
    return problems


def synthetic_doc(
    name: str = "things", table: str = "item", column: str = "price"
) -> dict[str, Any]:
    def col(n: str, t: str, **gen: Any) -> dict[str, Any]:
        return {"name": n, "type": t, "generator": {"strategy": "sequence", **gen}}

    types = ["integer", "string", "decimal", "timestamp", "boolean", "uuid", "float", "date"]
    types += ["time", "binary", "mystery"]
    columns = {
        "id": col("id", "integer"),
        "owner_id": {
            "name": "owner_id",
            "type": "integer",
            "generator": {"strategy": "foreign_key", "ref": "owner.owner_id"},
        },
    }
    for t in types:
        columns[f"c_{t}"] = col(f"c_{t}", t)
    columns[column] = col(column, "decimal")
    return {
        "schema_version": 1,
        "model": {"name": "x", "domain": name, "schema_mode": "star", "locale": "de_DE", "seed": 1},
        "tables": {
            "owner": {
                "name": "owner",
                "description": "who owns an item",
                "primary_key": ["owner_id"],
                "columns": {
                    "owner_id": col("owner_id", "integer"),
                    "label": col("label", "string"),
                },
            },
            table: {"name": table, "primary_key": ["id"], "columns": columns},
        },
        "relationships": [
            {
                "name": f"{table}_owner",
                "parent": "owner",
                "child": table,
                "parent_columns": ["owner_id"],
                "child_columns": ["owner_id"],
            }
        ],
        "generation": {"scale": "small", "scales": {"small": {"owner": 3, table: 9}}},
    }


def shape_bim(
    doc: dict[str, Any], source_type: str, source_name: str, measures: bool, schema: str
) -> Any:
    from shape_fabric.semantic_model import SemanticModelExporter

    from shape.generation.schema import GenSchema

    return SemanticModelExporter().to_dict(
        GenSchema.from_dict(doc),
        source_type=source_type,
        source_name=source_name,
        include_measures=measures,
        schema_name=schema,
    )


def baseline_bim(
    doc: dict[str, Any], source_type: str, source_name: str, measures: bool, schema: str, tmp: Path
) -> Any:
    path = tmp / "doc.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return baseline_json(
        "baseline_bim.py", [str(path), source_type, source_name, "1" if measures else "0", schema]
    )


def check_export_library(tmp: Path) -> Problems:
    problems: Problems = []
    doc = synthetic_doc()
    for source_type in ("lakehouse", "warehouse", "sql_database"):
        for measures in (True, False):
            base = baseline_bim(doc, source_type, "Src", measures, "gen", tmp)
            mine = shape_bim(doc, source_type, "Src", measures, "gen")
            problems += [
                f"[{source_type} measures={measures}] {p}" for p in compare_bim(base, mine)
            ]
    return problems


# ---------------------------------------------------------------------------------------------
# notebook

TARGETS = ("lakehouse", "csv", "display")


def structure(nb: dict[str, Any]) -> dict[str, Any]:
    return {
        "nbformat": nb["nbformat"],
        "nbformat_minor": nb["nbformat_minor"],
        "metadata_keys": sorted(nb["metadata"]),
        "kernelspec": nb["metadata"]["kernelspec"],
        "cells": [
            {"kind": c["cell_type"], "keys": sorted(c), "lines": bool(c["source"])}
            for c in nb["cells"]
        ],
    }


def check_notebook(tmp: Path) -> Problems:
    problems: Problems = []
    for target in TARGETS:
        got = {}
        for tool, call in (("baseline", baseline), ("shape", shape)):
            cwd = tmp / f"nb-{target}-{tool}"
            cwd.mkdir()
            code, out, err = call(
                [
                    "notebook",
                    "retail",
                    "-s",
                    "small",
                    "--seed",
                    "7",
                    "--target",
                    target,
                    "-o",
                    "n.ipynb",
                ],
                cwd,
            )
            if code != 0:
                raise RunError(f"{tool} notebook --target {target} exited {code}: {err[-300:]}")
            got[tool] = (out, json.loads((cwd / "n.ipynb").read_text(encoding="utf-8")))
        problems += [
            f"[{target}] {p}"
            for p in diff(structure(got["baseline"][1]), structure(got["shape"][1]))
        ]
        problems += [
            f"[{target}] report {p}" for p in compare_text(got["baseline"][0], got["shape"][0])
        ]
        # to standard output, as JSON, when there is no -o
        printed = {}
        for tool, call in (("baseline", baseline), ("shape", shape)):
            code, out, _ = call(["notebook", "retail", "--target", target], tmp)
            printed[tool] = json.loads(out)
        problems += [
            f"[{target} stdout] {p}"
            for p in diff(structure(printed["baseline"]), structure(printed["shape"]))
        ]
    return problems


def check_exit_codes(tmp: Path) -> Problems:
    """A wrong command line fails in both; Shape's code is 2 (the convention, an allowed entry)."""
    problems: Problems = []
    cases = [
        ["notebook", "nonesuch"],
        ["export-model", "nonesuch"],
        ["publish", "retail", "-t", "lakehouse"],
        ["deploy-notebook", "retail"],
    ]
    for argv in cases:
        b = baseline(argv, tmp)[0]
        s = shape(argv, tmp)[0]
        if b == 0 or s != 2:
            problems.append(
                f"{' '.join(argv)}: baseline exit {b}, shape exit {s} (want non-zero, 2)"
            )
    return problems


# ---------------------------------------------------------------------------------------------
# deploy-notebook and setup-fabric: the conversation


def shape_conversation(
    command: str, argv: list[str], *, accepted: bool = False, paged: bool = False
) -> dict[str, Any]:
    """Run Shape's command in this process against the fake Fabric service; the same record as
    ``baseline_rest.py`` prints."""
    import contextlib
    import io

    from shape_fabric import commands
    from shape_fabric.fabric_api import tuple_transport
    from shape_fabric.testing import FakeFabricItems, FakeIdentity

    from shape.cli.main import main

    fake = FakeFabricItems(
        workspaces=(
            [("Other", "22222222-2222-4222-8222-222222222222"), ("Demo", WORKSPACE)]
            if paged
            else [("Demo", WORKSPACE)]
        ),
        page_size=1 if paged else 100,
        accepted=accepted,
    )
    calls: list[dict[str, Any]] = []

    def recording(method: str, url: str, headers: Any, body: bytes, timeout: float) -> Any:
        doc = json.loads(body) if body else None
        if doc and "definition" in doc:
            for part in doc["definition"]["parts"]:
                part["payload"] = base64.b64decode(part["payload"]).decode("utf-8")
        path = url.split("fabric.microsoft.com", 1)[1].split("?", 1)[0]
        calls.append({"method": method, "path": path, "body": doc})
        return fake(method, url, headers, body, timeout)

    out = io.StringIO()
    with FakeIdentity().installed(), contextlib.redirect_stdout(out):
        saved = commands.API_TRANSPORT
        commands.API_TRANSPORT = tuple_transport(recording)
        try:
            code = main([command, *argv])
        finally:
            commands.API_TRANSPORT = saved
    return {"exit": code, "stdout": out.getvalue(), "calls": calls}


def notebook_parts(call: dict[str, Any]) -> dict[str, str]:
    return {p["path"]: p["payload"] for p in call["body"]["definition"]["parts"]}


def check_rest(tmp: Path) -> Problems:
    problems: Problems = []
    # deploy-notebook
    b = baseline_json(
        "baseline_rest.py",
        ["deploy", "retail", "--workspace", "Demo", "-s", "small", "--seed", "7"],
    )
    s = shape_conversation(
        "deploy-notebook", ["retail", "--workspace", "Demo", "-s", "small", "--seed", "7"]
    )
    if b["exit"] != 0 or s["exit"] != 0:
        raise RunError(f"deploy-notebook exited {b['exit']} / {s['exit']}")
    problems += [
        f"[deploy] {p}"
        for p in diff(
            [(c["method"], c["path"]) for c in b["calls"]],
            [(c["method"], c["path"]) for c in s["calls"]],
        )
    ]
    bc, sc = b["calls"][-1]["body"], s["calls"][-1]["body"]
    problems += [
        f"[deploy] item {p}"
        for p in diff(
            {
                "displayName": brand(bc["displayName"]),
                "type": bc["type"],
                "format": bc["definition"]["format"],
            },
            {
                "displayName": sc["displayName"],
                "type": sc["type"],
                "format": sc["definition"]["format"],
            },
        )
    ]
    bnb = json.loads(next(iter(notebook_parts(b["calls"][-1]).values())))
    snb = json.loads(notebook_parts(s["calls"][-1])["notebook-content.ipynb"])
    problems += [f"[deploy] notebook {p}" for p in diff(structure(bnb), structure(snb))]
    problems += [f"[deploy] report {p}" for p in compare_text(b["stdout"], s["stdout"])]
    # setup-fabric
    b = baseline_json("baseline_rest.py", ["setup", "--workspace", "Demo", "--create-lakehouse"])
    s = shape_conversation("setup-fabric", ["--workspace", "Demo", "--create-lakehouse"])
    if b["exit"] != 0 or s["exit"] != 0:
        raise RunError(f"setup-fabric exited {b['exit']} / {s['exit']}")
    norm = lambda calls: [  # noqa: E731
        {"method": c["method"], "path": c["path"], "body": c["body"]} for c in calls
    ]
    problems += [
        f"[setup] {p}"
        for p in diff(json.loads(brand(json.dumps(norm(b["calls"])))), norm(s["calls"]))
    ]
    # the baseline's next-steps list names one library; Shape's names the domains package too
    problems += [
        f"[setup] report {p}"
        for p in compare_text(b["stdout"], s["stdout"], drop=("- sqllocks-shape-domains",))
    ]
    return problems


# ---------------------------------------------------------------------------------------------
# publish

FORMATS = {"parquet": "part-0001.parquet", "csv": "part-0001.csv", "jsonl": "part-0001.jsonl"}


def rows_in(path: Path) -> int:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        return int(pq.read_metadata(path).num_rows)
    count = len(path.read_text(encoding="utf-8").splitlines())
    return count - 1 if path.suffix == ".csv" else count


def columns_in(path: Path) -> list[str]:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq  # type: ignore[import-untyped]

        return list(pq.read_schema(path).names)
    first = path.read_text(encoding="utf-8").splitlines()[0]
    if path.suffix == ".csv":
        return [c.strip('"') for c in first.split(",")]
    return list(json.loads(first))


def landing(root: Path) -> dict[str, Path]:
    return {str(p.relative_to(root)): p for p in sorted(root.rglob("*")) if p.is_file()}


SHAPE_ONLY_MANIFEST_KEYS = frozenset({"format", "version", "reproducibility", "dataset_id"})


def compare_manifest(base: dict[str, Any], mine: dict[str, Any], *, seed: int) -> Problems:
    problems: Problems = []
    # Shape's run manifest declares its format and version and carries the
    # reproducibility tuple and dataset id (W1-03, plan 2.3). Exactly these keys
    # are Shape's own; every other key must match the baseline's key set.
    extra = sorted(set(mine) - set(base))
    if extra != sorted(SHAPE_ONLY_MANIFEST_KEYS) or sorted(base) != sorted(
        set(mine) - SHAPE_ONLY_MANIFEST_KEYS
    ):
        problems.append(f"manifest keys: {sorted(base)} != {sorted(mine)}")
    elif mine["format"] != "shape-run-manifest" or type(mine["version"]) is not int:
        problems.append(f"manifest declaration: {mine['format']!r} version {mine['version']!r}")
    for key in (
        "spec_hash",
        "pack_id",
        "domain",
        "scale",
        "seed",
        "outputs",
        "validation",
        "chaos",
    ):
        if base.get(key) != mine.get(key):
            problems.append(f"manifest {key}: {base.get(key)!r} != {mine.get(key)!r}")
    for doc, tag in ((base, "baseline"), (mine, "shape")):
        if not re.fullmatch(
            rf"\d{{8}}_\d{{6}}_retail_small_s{seed}", brand(doc["run_id"])
        ) and not re.fullmatch(rf"\d{{8}}_\d{{6}}_retail_small_s{seed}", doc["run_id"]):
            problems.append(
                f"{tag} run_id {doc['run_id']!r} is not YYYYMMDD_HHMMSS_domain_scale_sSEED"
            )
    if sorted(base["timestamps"]) != sorted(mine["timestamps"]):
        problems.append("manifest timestamps keys differ")
    bt = {t: (v["rows"], v["columns"]) for t, v in base["tables"].items()}
    mt = {t: (v["rows"], v["columns"]) for t, v in mine["tables"].items()}
    problems += [f"manifest tables {p}" for p in diff(bt, mt)]
    if (
        sorted(base["tables"])
        and [list(v) for v in base["tables"].values()]
        and any(
            sorted(v) != sorted(next(iter(mine["tables"].values())))
            for v in base["tables"].values()
        )
    ):
        problems.append("a table entry has other keys")
    missing = [
        k
        for k in base["sbom"]
        if k.replace("sqllocks-spindle", "sqllocks-shape") not in mine["sbom"]
    ]
    if missing:
        problems.append(f"sbom lacks {missing}")
    for doc, tag in ((base, "baseline"), (mine, "shape")):
        if doc["engine_version"] != doc["sbom"].get(
            "sqllocks-spindle" if tag == "baseline" else "sqllocks-shape"
        ):
            problems.append(f"{tag} engine_version differs from its own sbom entry")
    return problems


def publish_run(
    tool: str, call: Callable[..., Any], tmp: Path, fmt: str, seed: int
) -> tuple[Path, str]:
    root = tmp / f"pub-{tool}-{fmt}" / "lh"
    cwd = root.parent
    cwd.mkdir(parents=True)
    code, out, err = call(
        [
            "publish",
            "retail",
            "--target",
            "lakehouse",
            "--base-path",
            str(root),
            "--format",
            fmt,
            "--seed",
            str(seed),
        ],
        cwd,
    )
    if code != 0:
        raise RunError(f"{tool} publish --format {fmt} exited {code}: {err[-300:]}{out[-300:]}")
    return root, out


def report_rows(text: str) -> dict[str, int]:
    return {
        m.group(1): int(m.group(2).replace(",", ""))
        for m in re.finditer(r"^  (\w+): ([\d,]+) rows", text, re.M)
    }


def compare_landing(broot: Path, sroot: Path) -> Problems:
    """The files under two lakehouse folders: the same set, with the same rows and columns."""
    problems: Problems = []
    bfiles, sfiles = landing(broot), landing(sroot)
    problems += [f"files {p}" for p in diff(sorted(bfiles), sorted(sfiles))]
    for rel in sorted(set(bfiles) & set(sfiles)):
        if rel.endswith(".json"):
            continue
        if rows_in(bfiles[rel]) != rows_in(sfiles[rel]):
            problems.append(f"{rel}: {rows_in(bfiles[rel])} rows != {rows_in(sfiles[rel])}")
        if columns_in(bfiles[rel]) != columns_in(sfiles[rel]):
            problems.append(f"{rel}: columns differ")
    return problems


def check_publish(tmp: Path) -> Problems:
    problems: Problems = []
    seed = 7
    manifests: dict[str, dict[str, Any]] = {}
    for fmt in FORMATS:
        broot, bout = publish_run("baseline", baseline, tmp, fmt, seed)
        sroot, sout = publish_run("shape", shape, tmp, fmt, seed)
        problems += [f"[{fmt}] {p}" for p in compare_landing(broot, sroot)]
        bfiles, sfiles = landing(broot), landing(sroot)
        problems += [
            f"[{fmt}] reported rows {p}" for p in diff(report_rows(bout), report_rows(sout))
        ]
        for line in (
            "Publish complete.",
            "Published 9 tables to lakehouse.",
            f"Scale: small | Seed: {seed} | Format: {fmt}",
        ):
            if line not in bout or line not in sout:
                problems.append(f"[{fmt}] report lacks {line!r}")
        manifest_rel = "landing/retail/manifest/_control/run_manifest.json"
        if manifest_rel in bfiles and manifest_rel in sfiles:
            bm = json.loads(bfiles[manifest_rel].read_text(encoding="utf-8"))
            sm = json.loads(sfiles[manifest_rel].read_text(encoding="utf-8"))
            problems += [f"[{fmt}] {p}" for p in compare_manifest(bm, sm, seed=seed)]
            manifests[fmt] = sm
    # a dry run writes nothing and says so
    for tool, call in (("baseline", baseline), ("shape", shape)):
        root = tmp / f"dry-{tool}" / "lh"
        root.parent.mkdir(parents=True)
        code, out, _ = call(
            ["publish", "retail", "-t", "lakehouse", "--base-path", str(root), "--dry-run"],
            root.parent,
        )
        if code != 0 or root.exists() or "Dry run complete. No data published." not in out:
            problems.append(f"{tool} --dry-run: exit {code}, wrote={root.exists()}")
    return problems


# ---------------------------------------------------------------------------------------------
# probes of the allowed differences (each must observe what its entry says)


def probe_quoting(tmp: Path) -> Problems:
    problems: Problems = []
    hostile = 'we"ird'
    doc = synthetic_doc(table=hostile, column="a]b")
    doc["tables"][hostile]["description"] = ""
    base = baseline_bim(doc, "warehouse", 's"v', True, "dbo", tmp)
    mine = shape_bim(doc, "warehouse", 's"v', True, "dbo")
    b_table = next(t for t in base["model"]["tables"] if t["name"] == hostile)
    s_table = next(t for t in mine["model"]["tables"] if t["name"] == hostile)
    b_expr = b_table["partitions"][0]["source"]["expression"]
    s_expr = s_table["partitions"][0]["source"]["expression"]
    if 'Item="we"ird"' not in b_expr or 'Sql.Database("s"v"' not in b_expr:
        problems.append("the baseline no longer leaves a quote in an M literal unquoted")
    if 'Item="we""ird"' not in s_expr or 'Sql.Database("s""v"' not in s_expr:
        problems.append("shape does not double the quote in an M literal")
    b_dax = [m["expression"] for m in b_table["measures"]]
    s_dax = [m["expression"] for m in s_table["measures"]]
    if not any("[a]b]" in e for e in b_dax):
        problems.append(
            "the baseline no longer leaves a closing bracket in a DAX reference unquoted"
        )
    if not any("[a]]b]" in e for e in s_dax):
        problems.append("shape does not double the closing bracket in a DAX reference")
    # and that is all that differs
    rest_b, rest_s = copy.deepcopy(base), copy.deepcopy(mine)
    for doc_ in (rest_b, rest_s):
        for t in doc_["model"]["tables"]:
            t["partitions"] = []
            if t["name"] == hostile:
                t.pop("measures", None)
    problems += [f"[quoting] {p}" for p in compare_bim(rest_b, rest_s)]
    return problems


def probe_notebook_part(tmp: Path) -> Problems:
    b = baseline_json("baseline_rest.py", ["deploy", "retail", "--workspace", "Demo"])
    s = shape_conversation("deploy-notebook", ["retail", "--workspace", "Demo"])
    bparts, sparts = notebook_parts(b["calls"][-1]), notebook_parts(s["calls"][-1])
    problems: Problems = []
    if list(bparts) != ["notebook-content.py"]:
        problems.append(f"the baseline's parts are now {list(bparts)}")
    if sorted(sparts) != [".platform", "notebook-content.ipynb"]:
        problems.append(f"shape's parts are {sorted(sparts)}")
    return problems


def probe_accepted(tmp: Path) -> Problems:
    b = baseline_json(
        "baseline_rest.py", ["deploy", "retail", "--workspace", "Demo"], {"FAKE_ACCEPTED": "1"}
    )
    s = shape_conversation("deploy-notebook", ["retail", "--workspace", "Demo"], accepted=True)
    problems: Problems = []
    if not (b["exit"] == 0 and "Item ID: unknown" in b["stdout"]):
        problems.append("the baseline no longer reports success with 'Item ID: unknown' on a 202")
    if not (s["exit"] == 0 and "Item ID: unknown" not in s["stdout"] and "Item ID:" in s["stdout"]):
        problems.append("shape did not follow the accepted creation to a real item")
    return problems


def probe_pagination(tmp: Path) -> Problems:
    b = baseline_json(
        "baseline_rest.py", ["deploy", "retail", "--workspace", "Demo"], {"FAKE_PAGED": "1"}
    )
    s = shape_conversation("deploy-notebook", ["retail", "--workspace", "Demo"], paged=True)
    problems: Problems = []
    if b["exit"] == 0:
        problems.append("the baseline now finds a workspace on the second page")
    if s["exit"] != 0:
        problems.append("shape did not find a workspace on the second page")
    return problems


def baseline_source() -> str:
    return (SPINDLE_ROOT / "sqllocks_spindle" / "cli.py").read_text(encoding="utf-8")


def probe_baseline_layouts(tmp: Path) -> Problems:
    text = baseline_source()
    remote = 'f"{lakehouse_prefix}/Files/landing/{domain_name}/{table_name}/latest"' in text
    control = 'f"{lakehouse_prefix}/Files/_control/{domain_name}"' in text
    local = 'landing_zone_path(domain_name, table_name, "latest")' in text
    return (
        []
        if remote and control and local
        else ["the baseline's publish layouts are not as the entry says"]
    )


def probe_baseline_delta(tmp: Path) -> Problems:
    text = baseline_source()
    start = text.index("# Serialize DataFrame to bytes")
    block = text[start : text.index("data = buf.getvalue()", start)]
    if "delta" in block:
        return ["the baseline's remote branch now serialises delta"]
    return []


def probe_manifest_paths(tmp: Path) -> Problems:
    problems: Problems = []
    bdir = tmp / "paths-baseline"
    bdir.mkdir()
    code, _, err = baseline(
        ["publish", "retail", "-t", "lakehouse", "--base-path", str(bdir / "lh")], bdir
    )
    if code != 0:
        raise RunError(err[-300:])
    bm = json.loads((bdir / "lh/landing/retail/manifest/_control/run_manifest.json").read_text())
    sdir = tmp / "paths-shape"
    sdir.mkdir()
    shape(["publish", "retail", "-t", "lakehouse", "--base-path", str(sdir / "lh")], sdir)
    sm = json.loads((sdir / "lh/landing/retail/manifest/_control/run_manifest.json").read_text())
    if any(v["file_paths"] for v in bm["tables"].values()):
        problems.append("the baseline's manifest now lists files")
    for table, v in sm["tables"].items():
        if len(v["file_paths"]) != 1 or not Path(v["file_paths"][0]).is_file():
            problems.append(f"shape's manifest does not list the file of {table}")
    return problems


# ---------------------------------------------------------------------------------------------
# negative control


def negative_control(tmp: Path) -> Problems:
    """Each tampering must be flagged by the comparison that gates the real runs."""
    problems: Problems = []
    cwd = tmp / "nc"
    cwd.mkdir()
    shape(
        [
            "export-model",
            "retail",
            "-o",
            "model.bim",
            "--source-type",
            "warehouse",
            "--source-name",
            "W",
        ],
        cwd,
    )
    baseline(
        [
            "export-model",
            "retail",
            "-o",
            "base.bim",
            "--source-type",
            "warehouse",
            "--source-name",
            "W",
        ],
        cwd,
    )
    base = json.loads((cwd / "base.bim").read_text())
    mine = json.loads((cwd / "model.bim").read_text())
    if compare_bim(base, mine):
        raise RunError("the untampered export differs; the control cannot run")

    def flagged(name: str, tamper: Callable[[dict[str, Any]], None]) -> None:
        bad = copy.deepcopy(mine)
        tamper(bad)
        if not compare_bim(base, bad):
            problems.append(f"NOT FLAGGED: {name}")

    first = lambda d: d["model"]["tables"][0]  # noqa: E731
    flagged(
        "a measure expression changed",
        lambda d: first(d)["measures"][0].update(expression="COUNTROWS('x')"),
    )
    flagged("a column type changed", lambda d: first(d)["columns"][0].update(dataType="string"))
    flagged("a relationship dropped", lambda d: d["model"]["relationships"].pop())
    flagged("a measure dropped", lambda d: first(d)["measures"].pop())
    flagged(
        "a partition expression changed",
        lambda d: first(d)["partitions"][0]["source"].update(expression="x"),
    )
    flagged("the compatibility level changed", lambda d: d.update(compatibilityLevel=1500))
    flagged("a key flag removed", lambda d: [c.pop("isKey", None) for c in first(d)["columns"]])
    flagged("the model name changed", lambda d: d.update(name="Other"))

    # the manifest comparison
    pdir = tmp / "nc-pub"
    pdir.mkdir()
    shape(["publish", "retail", "-t", "lakehouse", "--base-path", str(pdir / "lh")], pdir)
    sm = json.loads((pdir / "lh/landing/retail/manifest/_control/run_manifest.json").read_text())
    bdir = tmp / "nc-pub-base"
    bdir.mkdir()
    baseline(["publish", "retail", "-t", "lakehouse", "--base-path", str(bdir / "lh")], bdir)
    bm = json.loads((bdir / "lh/landing/retail/manifest/_control/run_manifest.json").read_text())
    if compare_manifest(bm, sm, seed=42):
        raise RunError("the untampered manifest differs; the control cannot run")
    for name, tamper in (
        ("a table's rows changed", lambda m: next(iter(m["tables"].values())).update(rows=1)),
        ("a table dropped", lambda m: m["tables"].pop(next(iter(m["tables"])))),
        ("the seed changed", lambda m: m.update(seed=1)),
        ("a key removed", lambda m: m.pop("chaos")),
        ("the run id malformed", lambda m: m.update(run_id="latest")),
        ("Shape's dataset id removed", lambda m: m.pop("dataset_id")),
        ("Shape's format declaration changed", lambda m: m.update(format="other")),
        ("Shape's version not an integer", lambda m: m.update(version="1")),
        ("an unknown key added", lambda m: m.update(extra=1)),
    ):
        bad = copy.deepcopy(sm)
        tamper(bad)
        if not compare_manifest(bm, bad, seed=42):
            problems.append(f"NOT FLAGGED: manifest: {name}")

    # the landing comparison
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    sroot, broot = pdir / "lh", bdir / "lh"
    if compare_landing(broot, sroot):
        raise RunError("the untampered landing zones differ; the control cannot run")
    target = sroot / "landing/retail/customer/dt=latest/part-0001.parquet"
    original = pq.read_table(target)
    renamed = original.rename_columns(["x", *original.column_names[1:]])
    controls: list[tuple[str, Callable[[], None]]] = [
        ("rows dropped from a table", lambda: pq.write_table(original.slice(0, 5), target)),
        ("a column renamed", lambda: pq.write_table(renamed, target)),
        ("a table's file removed", lambda: target.unlink()),
    ]
    for name, tamper in controls:
        tamper()
        if not compare_landing(broot, sroot):
            problems.append(f"NOT FLAGGED: landing: {name}")
        pq.write_table(original, target)
    if compare_landing(broot, sroot):
        raise RunError("restoring the landing zone did not restore equality")

    # the notebook comparison
    nbdir = tmp / "nc-nb"
    nbdir.mkdir()
    baseline(["notebook", "retail", "-o", "b.ipynb"], nbdir)
    shape(["notebook", "retail", "-o", "s.ipynb"], nbdir)
    bnb = json.loads((nbdir / "b.ipynb").read_text())
    snb = json.loads((nbdir / "s.ipynb").read_text())
    if diff(structure(bnb), structure(snb)):
        raise RunError("the untampered notebooks differ; the control cannot run")
    for name, tamper in (
        ("a cell dropped", lambda d: d["cells"].pop()),
        ("a cell's kind changed", lambda d: d["cells"][0].update(cell_type="code")),
        ("the format version changed", lambda d: d.update(nbformat=3)),
    ):
        bad = copy.deepcopy(snb)
        tamper(bad)
        if not diff(structure(bnb), structure(bad)):
            problems.append(f"NOT FLAGGED: notebook: {name}")

    # the request comparison
    b = baseline_json("baseline_rest.py", ["setup", "--workspace", "Demo", "--create-lakehouse"])
    s = shape_conversation("setup-fabric", ["--workspace", "Demo", "--create-lakehouse"])
    norm = lambda calls: [  # noqa: E731
        {"method": c["method"], "path": c["path"], "body": c["body"]} for c in calls
    ]
    reference = json.loads(brand(json.dumps(norm(b["calls"]))))
    if diff(reference, norm(s["calls"])):
        raise RunError("the untampered requests differ; the control cannot run")
    for name, tamper in (
        ("an item type changed", lambda c: c[1]["body"].update(type="Warehouse")),
        ("a request dropped", lambda c: c.pop()),
        ("a path changed", lambda c: c[1].update(path="/v1/workspaces/x/items")),
        ("a method changed", lambda c: c[0].update(method="POST")),
    ):
        bad = copy.deepcopy(norm(s["calls"]))
        tamper(bad)
        if not diff(reference, bad):
            problems.append(f"NOT FLAGGED: requests: {name}")
    return problems


# ---------------------------------------------------------------------------------------------


CHECKS: tuple[tuple[str, Callable[[Path], Problems]], ...] = (
    ("export-model: every option, the .bim and the report", check_export_cli),
    (
        "export-model: the library on synthetic schemas (every type, key, source)",
        check_export_library,
    ),
    ("notebook: cells, metadata and report for each target", check_notebook),
    ("deploy-notebook / setup-fabric: requests and report", check_rest),
    ("publish: landing zone, manifest and report for each format; dry run", check_publish),
    ("wrong command lines fail in both (Shape: exit 2)", check_exit_codes),
    ("probe: M and DAX quoting", probe_quoting),
    ("probe: the notebook part path", probe_notebook_part),
    ("probe: a 202 is not 'created'", probe_accepted),
    ("probe: workspace listing pages", probe_pagination),
    ("probe: the manifest lists files", probe_manifest_paths),
    ("probe: the baseline's two landing layouts", probe_baseline_layouts),
    ("probe: the baseline's remote delta", probe_baseline_delta),
)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument(
        "--negative-control", action="store_true", help="also tamper and require a flag"
    )
    ap.add_argument("--keep", metavar="DIR", help="work in DIR and keep it")
    args = ap.parse_args(argv)
    failed = 0
    with tempfile.TemporaryDirectory() as scratch:
        tmp = Path(args.keep) if args.keep else Path(scratch)
        tmp.mkdir(parents=True, exist_ok=True)
        checks = list(CHECKS)
        if args.negative_control:
            checks.append(("negative control: tampering is flagged", negative_control))
        for title, check in checks:
            sub = tmp / re.sub(r"\W+", "-", title)[:40]
            sub.mkdir(parents=True, exist_ok=True)
            try:
                problems = check(sub)
            except RunError as exc:
                print(f"ERROR {title}: {exc}")
                return 2
            print(f"{'PASS' if not problems else 'FAIL'}  {title}")
            for p in problems[:12]:
                print(f"        {p}")
            if len(problems) > 12:
                print(f"        ... {len(problems) - 12} more")
            failed += bool(problems)
    print(f"\nallowed differences: {', '.join(d.name for d in ALLOWED)}")
    print(f"VERDICT: {'PASS' if not failed else 'FAIL'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
