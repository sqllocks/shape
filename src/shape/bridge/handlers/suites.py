"""``suite_list`` and ``suite_run`` (bridge 1.2): the starter scenario library and its suites.

``suite_list`` is what ``shape pack list --library --json`` prints. ``suite_run`` runs what
``shape suite run --json`` runs (``shape.scenario.library``) and returns its document: per scenario
the answer key's ``mismatches`` (``expected`` and ``observed``, empty when it was met), the observed
``outcome`` and ``met``, with ``met`` and ``passed`` for the suite. A scenario that missed its key
is a result (``passed: false``), not an error: the command line's exit code 1 is ``passed: false``.

All names are checked before anything runs, so a typo in a suite runs nothing. The suite stops
between scenarios when the job is cancelled; the cancelled job holds the counts so far. Suites run
and write locally only: an ``output_dir`` that is a URL answers ``policy.not_permitted``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.chaos import not_local
from shape.bridge.handlers.common import ANY, BOOL, INT, NUM, STR, STRS, arr, mapping, nullable, obj
from shape.bridge.handlers.rules import _check_version
from shape.bridge.jobs import JobCancelled
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command


def run_scenario(name: str, **kwargs: Any) -> Any:
    from shape.scenario.library import run_scenario as run

    return run(name, **kwargs)


def prepare_run(args: dict[str, Any], ctx: Context) -> None:
    from shape.scenario.library import SUITE_FORMAT, list_suites
    from shape.scenario.library.formats import VERSION

    not_local(args.get("output_dir"), "output_dir")
    target = str(args["suite"])
    if target in list_suites():
        return
    if not Path(target).is_file():
        raise BridgeError(
            "input.not_found",
            f"no suite {target!r}: not a file, and the built-in suites are "
            f"{', '.join(list_suites())}",
            "run `suite_list`",
        )
    _check_version(target, "suite file", SUITE_FORMAT, VERSION)


def cmd_list(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.scenario.library import list_scenarios, list_suites

    return {"scenarios": list_scenarios(), "suites": list_suites()}


def cmd_run(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.bridge.errors import writing
    from shape.scenario.library import (
        LibraryError,
        ScenarioResult,
        SuiteResult,
        list_scenarios,
        load_suite,
    )

    prepare_run(args, ctx)
    doc = load_suite(str(args["suite"]))
    known = {e["id"] for e in list_scenarios()}
    missing = [s for s in doc["scenarios"] if s not in known]
    if missing:  # before anything runs
        raise LibraryError(
            f"suite {doc['name']} names unknown scenarios: {', '.join(missing)}; "
            f"the library has: {', '.join(sorted(known))}"
        )
    scale = args.get("scale")
    seed = None if args.get("seed") is None else int(args["seed"])
    output = args.get("output_dir")
    total = len(doc["scenarios"])
    result = SuiteResult(str(doc["name"]), scale)
    for done, name in enumerate(doc["scenarios"]):
        if ctx.cancel.is_set():
            met = sum(r.met for r in result.results)
            raise JobCancelled(
                {
                    "suite": result.name,
                    "scenarios_run": done,
                    "scenarios_total": total,
                    "met": met,
                    "not_met": done - met,
                }
            )
        ctx.progress({"scenario": name, "scenarios_run": done, "scenarios_total": total})
        with writing():
            one: ScenarioResult = run_scenario(name, scale=scale, seed=seed, output=output)
        result.results.append(one)
    out = result.to_dict()
    out["passed"] = result.met
    return out


_SCENARIO = obj({"id": STR, "domain": STR, "description": STR})
_RESULT = obj(
    {
        "suite": STR,
        "scale": nullable(STR),
        "met": BOOL,
        "passed": BOOL,
        "scenarios": arr(
            obj(
                {
                    "scenario": STR,
                    "met": BOOL,
                    "mismatches": arr(obj({"expected": STR, "observed": STR})),
                    "outcome": obj(
                        {
                            "scenario": STR,
                            "domain": STR,
                            "scale": STR,
                            "seed": INT,
                            "gates": mapping(BOOL),
                            "gate_messages": mapping(STR),
                            "defects": mapping(INT),
                            "drift": arr(ANY),
                            "files": STRS,
                            "elapsed_seconds": NUM,
                        }
                    ),
                }
            )
        ),
    }
)

COMMANDS = [
    Command(
        "suite_list",
        "List the built-in suites and the scenarios of the starter library.",
        {},
        obj({"suites": STRS, "scenarios": arr(_SCENARIO)}),
        cmd_list,
        since="1.2",
        effects=(),
    ),
    Command(
        "suite_run",
        "Run a suite of library scenarios and compare each outcome with its answer key "
        "(job-capable, cancellable between scenarios).",
        {
            "suite": Arg(
                "string",
                "a built-in suite name (see `suite_list`) or the path of a suite file "
                "(format shape-suite)",
                True,
                name_or_path=True,
            ),
            "scale": Arg(
                "string", "a scale preset of the scenarios' domain, or tiny (default: small)"
            ),
            "seed": Arg("integer", "the seed (default: each scenario's own)"),
            "output_dir": Arg(
                "string",
                "write each scenario's tables under output_dir/<scenario>/ (default: write "
                "nothing)",
                path="write",
            ),
        },
        _RESULT,
        cmd_run,
        job=True,
        cancellable=True,
        prepare=prepare_run,
        since="1.2",
        effects=("reads_files", "writes_files", "cancels"),
    ),
]
