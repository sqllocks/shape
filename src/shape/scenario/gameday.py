"""Game days: rehearse failures against local copies of a project's data.

A plan (format ``shape-gameday``) names a local data folder and rounds. For each round the
runner copies the named tables into ``DIR/round-N/``, plants the failure of a library scenario or
catalog entry in the copies, runs the round's checks as Shape subcommands in this process (only
``profile``, ``diff``, ``check``, ``verify`` and ``fidelity``; there is no shell) and records,
for each expectation, whether a check detected it, and the wall time. The source folder is never
written to. A plan is read and checked in full, and every table read, before the first round
runs. See ``docs/GAMEDAY.md``. Nothing heavy loads at import time (T-18).

In a check, ``{data}`` is the source folder, ``{round}`` this round's folder and ``{plan}`` the
folder of the plan file. A check may write only below ``{round}``.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.scenario import results
from shape.scenario.library import catalog, detect, formats
from shape.scenario.library.formats import LibraryError, check_header, read_json

GAMEDAY_FORMAT = "shape-gameday"
REPORT_FORMAT = "shape-gameday-report"
ALLOWED_COMMANDS = ("profile", "diff", "check", "verify", "fidelity")
DEFAULT_SEED = 42
_PLAN_KEYS = {"format", "version", "data", "rounds"}
_ROUND_KEYS = {"name", "inject", "tables", "checks", "expect"}
_WRITE_FLAGS = ("-o", "--output", "--json", "--junit", "--sarif", "--html", "--out", "--sign")
_READERS = {".csv": "csv", ".parquet": "parquet", ".jsonl": "jsonl", ".ndjson": "jsonl"}


class GamedayError(LibraryError):
    """A plan is malformed or names something that cannot be used; exit 2 before any round."""


# ---- reading and writing a table ----------------------------------------------------------------


def read_table(path: Path) -> Any:
    kind = _READERS.get(path.suffix.lower())
    if kind is None:
        raise GamedayError(
            f"{path.name}: a table is a {', '.join(sorted(_READERS))} file, not {path.suffix!r}"
        )
    try:
        if kind == "csv":
            import pyarrow.csv as pacsv  # type: ignore[import-untyped]

            return pacsv.read_csv(path)
        if kind == "parquet":
            import pyarrow.parquet as pq  # type: ignore[import-untyped]

            return pq.read_table(path)
        import pyarrow.json as pajson  # type: ignore[import-untyped]

        return pajson.read_json(path)
    except Exception as exc:  # noqa: BLE001 - every reader error means "not a readable table"
        raise GamedayError(f"{path.name} cannot be read as a table: {exc}") from None


def write_table(table: Any, path: Path) -> None:
    kind = _READERS[path.suffix.lower()]
    if kind == "csv":
        import pyarrow.csv as pacsv

        pacsv.write_csv(table, path)
    elif kind == "parquet":
        import pyarrow.parquet as pq

        pq.write_table(table, path)
    else:
        with path.open("w", encoding="utf-8") as fh:
            for row in table.to_pylist():
                fh.write(json.dumps(row, default=str) + "\n")


# ---- the plan -----------------------------------------------------------------------------------


@dataclass
class Round:
    name: str
    inject: str
    scenario: str
    tables: list[str]
    checks: list[list[str]]
    expect: list[str]


@dataclass
class Plan:
    path: Path
    data: Path
    rounds: list[Round] = field(default_factory=list)


def _text_list(value: Any, what: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or not all(isinstance(v, str) and v.strip() for v in value)
    ):
        raise GamedayError(f"{what} must be a non-empty list of text")
    return list(value)


def _check_command(argv: Any, what: str) -> list[str]:
    if not isinstance(argv, list) or not argv or not all(isinstance(w, str) and w for w in argv):
        raise GamedayError(f'{what} must be a list of words, such as ["diff", "a.shape", ...]')
    if argv[0] not in ALLOWED_COMMANDS:
        raise GamedayError(
            f"{what} runs {argv[0]!r}; a game day runs only {', '.join(ALLOWED_COMMANDS)} "
            f"(there are no shell commands)"
        )
    for i, word in enumerate(argv[1:], start=1):
        flag, _, glued = word.partition("=")
        if flag in _WRITE_FLAGS and glued:
            target = glued
        elif argv[i - 1] in _WRITE_FLAGS:
            target = word
        else:
            continue
        if target != "-" and (not target.startswith("{round}") or ".." in Path(target).parts):
            raise GamedayError(
                f"{what} writes to {target!r}; a check may write only below {{round}}"
            )
    return list(argv)


def parse_plan(doc: Any, path: Path, root: Path | None = None) -> Plan:
    """The plan ``doc`` read from ``path``, checked in full: the format and version, the data
    folder (a local folder that exists), every round's injection (a scenario or catalog id that
    plants defects in one batch), tables (files of the data folder), checks (only the allowed
    commands, writing only below ``{round}``) and expectations (checks Shape has). Raises
    :class:`GamedayError` before anything runs."""
    what = f"plan {path.name}"
    try:
        out = check_header(doc, GAMEDAY_FORMAT, what, _PLAN_KEYS)
    except LibraryError as exc:
        raise GamedayError(str(exc)) from None
    missing = sorted(_PLAN_KEYS - set(out))
    if missing:
        raise GamedayError(f"{what} lacks {', '.join(missing)}")
    raw = out["data"]
    if not isinstance(raw, str) or not raw.strip():
        raise GamedayError(f"{what}: 'data' must be the path of a local folder")
    if "://" in raw:
        raise GamedayError(f"{what}: 'data' is {raw!r}; a game day works on a local folder only")
    data = (path.parent / raw).resolve() if not Path(raw).is_absolute() else Path(raw).resolve()
    if not data.is_dir():
        raise GamedayError(f"{what}: 'data' {raw!r} is not a local folder")
    rounds = out["rounds"]
    if not isinstance(rounds, list) or not rounds:
        raise GamedayError(f"{what}: 'rounds' must be a non-empty list")
    plan = Plan(path, data)
    names: set[str] = set()
    for n, item in enumerate(rounds, start=1):
        label = f"{what}: round {n}"
        if not isinstance(item, dict) or set(item) != _ROUND_KEYS:
            raise GamedayError(f"{label} has {', '.join(sorted(_ROUND_KEYS))}")
        name = item["name"]
        if not isinstance(name, str) or not name.strip():
            raise GamedayError(f"{label}: 'name' must be text")
        if name in names:
            raise GamedayError(f"{what}: round name {name!r} is used twice")
        names.add(name)
        inject = item["inject"]
        if not isinstance(inject, str) or not inject.strip():
            raise GamedayError(f"{label}: 'inject' must be library:NAME or a failure mode id")
        scenario = _scenario_of(inject, label, root)
        tables = _text_list(item["tables"], f"{label}: 'tables'")
        files = [_resolve_table(data, t, label) for t in tables]
        checks = item["checks"]
        if not isinstance(checks, list) or not checks:
            raise GamedayError(f"{label}: 'checks' must be a non-empty list of commands")
        commands = [_check_command(c, f"{label}: a check") for c in checks]
        expect = _text_list(item["expect"], f"{label}: 'expect'")
        for e in expect:
            try:
                for alternative in e.split("|"):
                    detect.check_exists(alternative, f"{label}: an expectation")
            except LibraryError as exc:
                raise GamedayError(str(exc)) from None
        plan.rounds.append(Round(name, inject, scenario, [f.name for f in files], commands, expect))
    return plan


def _scenario_of(inject: str, label: str, root: Path | None) -> str:
    from shape.scenario.library.run import list_scenarios, load_scenario

    try:
        if inject.startswith("library:"):
            name = inject.partition(":")[2]
            if name not in {e["id"] for e in list_scenarios(root)}:
                raise GamedayError(f"{label}: {inject!r} is not a scenario of the library")
        else:
            name = catalog.scenario_of(catalog.get_mode(inject, root))
        spec = load_scenario(name, root)
    except GamedayError:
        raise
    except LibraryError as exc:
        raise GamedayError(f"{label}: {exc}") from None
    if spec.get("drift") or not spec.get("defects"):
        raise GamedayError(
            f"{label}: {inject!r} is a change over time, not a failure planted in a batch; "
            f"a game day injects scenarios that plant defects"
        )
    return name


def _resolve_table(data: Path, name: str, label: str) -> Path:
    exact = data / name
    if exact.is_file() and exact.suffix.lower() in _READERS:
        return exact
    matches = sorted(
        p for p in data.iterdir() if p.is_file() and p.stem == name and p.suffix.lower() in _READERS
    )
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise GamedayError(
            f"{label}: table {name!r} is in more than one file "
            f"({', '.join(m.name for m in matches)})"
        )
    raise GamedayError(f"{label}: table {name!r} is not a file of {data}")


def load_plan(path: str | Path, root: Path | None = None) -> Plan:
    p = Path(path)
    try:
        doc = read_json(p, "the plan")
    except LibraryError as exc:
        raise GamedayError(str(exc)) from None
    return parse_plan(doc, p, root)


# ---- the injection ------------------------------------------------------------------------------

_TEXT = ("truncate_strings", "placeholder_values", "corrupt_encoding")
_MOMENT = ("late_arrivals", "shift_hours", "chaos_temporal")


def _candidates(kind: str, table: Any) -> list[str]:
    """The columns of ``table`` an injection of ``kind`` may use, best first."""
    import pyarrow as pa

    cols = list(zip(table.column_names, table.schema.types, strict=True))
    text = [n for n, t in cols if pa.types.is_string(t) or pa.types.is_large_string(t)]
    moment = [n for n, t in cols if pa.types.is_timestamp(t)]
    floats = [n for n, t in cols if pa.types.is_floating(t)]
    ints = [n for n, t in cols if pa.types.is_integer(t)]
    names = [n for n, _ in cols]
    if kind in _TEXT:
        return text
    if kind in _MOMENT:
        return moment
    if kind == "scale_values":
        return floats + ints
    if kind == "orphan_keys":
        return ints
    if kind == "inject_nulls":
        return text + floats + names
    if kind == "shuffle_column":
        return text + names
    if kind == "drop_column":
        return list(reversed(names))
    if kind in ("rename_column", "retype_column", "duplicate_keys"):
        return text + names if kind == "retype_column" else names
    return names


@dataclass
class Injected:
    kind: str
    table: str
    column: str | None
    rows: int

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "table": self.table, "column": self.column, "rows": self.rows}


def inject(
    tables: dict[str, Any], scenario: str, seed: int, root: Path | None = None
) -> tuple[dict[str, Any], list[Injected]]:
    """Plant the defects of the library scenario ``scenario`` in ``tables`` (the user's tables, by
    name, in the order the plan lists them). The scenario's first table is the first of ``tables``,
    its second the second, and so on; in each, an injection takes the first column of the right
    kind. Raises :class:`GamedayError` when a table cannot take an injection."""
    import numpy as np

    from shape.scenario.library import run
    from shape.scenario.library.defects import DEFECTS, DefectError

    spec = run.load_scenario(scenario, root)
    defects = list(spec["defects"])
    wanted: list[str] = []
    for d in defects:
        if d["table"] not in wanted:
            wanted.append(str(d["table"]))
    names = list(tables)
    if len(names) < len(wanted):
        raise GamedayError(
            f"the injection {scenario!r} touches {len(wanted)} table(s); the round names "
            f"{len(names)}"
        )
    mapping = dict(zip(wanted, names, strict=False))
    out = dict(tables)
    done: list[Injected] = []
    for position, defect in enumerate(defects):
        name = mapping[str(defect["table"])]
        table = out[name]
        candidates = _candidates(str(defect["kind"]), table) or [None]  # type: ignore[list-item]
        error: Exception | None = None
        for column in candidates:
            attempt = dict(defect, table=name)
            if column is not None:
                attempt["column"] = column
            try:
                rng = np.random.default_rng([seed, position])
                out[name], rows = DEFECTS[str(defect["kind"])](table, attempt, rng, None)
            except (DefectError, ValueError, KeyError, TypeError) as exc:
                error = exc
                continue
            done.append(Injected(str(defect["kind"]), name, column, int(rows)))
            break
        else:
            raise GamedayError(
                f"table {name!r} cannot take the injection {defect['kind']!r}: {error}"
            )
    return out, done


# ---- running a check ----------------------------------------------------------------------------


def _json_flag(command: str) -> list[str]:
    """``--json`` (a switch) or ``--json -`` (a file, here standard output) for ``command``."""
    from shape.cli.introspect import core_commands

    parser = next(c.parser for c in core_commands() if c.path == command)
    act = next((a for a in parser._actions if "--json" in a.option_strings), None)
    if act is None:
        return []
    return ["--json", "-"] if act.nargs != 0 else ["--json"]


def run_check(argv: list[str]) -> tuple[int, str, str, float]:
    """Run one Shape subcommand in this process: ``(exit code, stdout, stderr, seconds)``."""
    from shape.cli.main import main

    flags = _json_flag(argv[0]) if argv[0] in results.SUPPORTED_COMMANDS else []
    out, err = io.StringIO(), io.StringIO()
    started = time.perf_counter()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main([*argv, *flags])  # type: ignore[no-untyped-call]
        except SystemExit as exc:  # argparse exits for an argument it cannot parse
            code = exc.code if isinstance(exc.code, int) else 2
    return int(code or 0), out.getvalue(), err.getvalue(), time.perf_counter() - started


# ---- a run --------------------------------------------------------------------------------------


@dataclass
class RoundReport:
    name: str
    inject: str
    scenario: str
    tables: list[str]
    injected: list[Injected] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    fired: list[str] = field(default_factory=list)
    expectations: list[dict[str, Any]] = field(default_factory=list)
    seconds: float = 0.0

    @property
    def detected(self) -> bool:
        return all(e["detected"] for e in self.expectations)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "inject": self.inject,
            "scenario": self.scenario,
            "tables": self.tables,
            "injected": [i.to_dict() for i in self.injected],
            "checks": self.checks,
            "fired": self.fired,
            "expectations": self.expectations,
            "detected": self.detected,
            "seconds": round(self.seconds, 3),
        }


@dataclass
class Report:
    plan: str
    data: str
    seed: int
    rounds: list[RoundReport] = field(default_factory=list)
    seconds: float = 0.0
    dry_run: bool = False

    @property
    def detected(self) -> bool:
        return all(r.detected for r in self.rounds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": REPORT_FORMAT,
            "version": formats.VERSION,
            "plan": self.plan,
            "data": self.data,
            "seed": self.seed,
            "detected": self.detected,
            "seconds": round(self.seconds, 3),
            "rounds": [r.to_dict() for r in self.rounds],
        }

    def markdown(self) -> str:
        verdict = "every expectation was detected" if self.detected else "a failure was missed"
        lines = [
            "# Game day report",
            "",
            f"Plan: `{self.plan}`  ",
            f"Data: `{self.data}`  ",
            f"Seed: {self.seed}  ",
            f"Result: **{verdict}** ({self.seconds:.1f} s)",
            "",
        ]
        for r in self.rounds:
            lines += [
                f"## {r.name}: {'detected' if r.detected else 'MISSED'}",
                "",
                f"Injected `{r.inject}` into {', '.join(r.tables)} ({r.seconds:.1f} s).",
                "",
            ]
            lines += [
                f"- planted `{i.kind}` in `{i.table}`"
                + (f".`{i.column}`" if i.column else "")
                + f" ({i.rows} rows)"
                for i in r.injected
            ]
            lines += ["", "| Expectation | Result |", "|---|---|"]
            lines += [
                f"| `{e['expect']}` | {'detected' if e['detected'] else 'MISSED'} |"
                for e in r.expectations
            ]
            lines += ["", "| Check | Exit code | Seconds |", "|---|---|---|"]
            lines += [
                f"| `{' '.join(c['command'])}` | {c['exit_code']} | {c['seconds']:.2f} |"
                for c in r.checks
            ]
            lines.append("")
        return "\n".join(lines).rstrip("\n") + "\n"


def _substitute(argv: list[str], values: dict[str, str]) -> list[str]:
    out = []
    for word in argv:
        for key, value in values.items():
            word = word.replace(key, value)
        out.append(word)
    return out


def run(
    plan_path: str | Path,
    output: str | Path,
    *,
    seed: int = DEFAULT_SEED,
    dry_run: bool = False,
    root: Path | None = None,
) -> Report:
    """Run the game day of ``plan_path`` into ``output``. The plan is checked and every table of
    every round read before the first round runs (:class:`GamedayError` otherwise). With
    ``dry_run`` the injections are worked out and nothing is written or run."""
    plan = load_plan(plan_path, root)
    out = Path(output).resolve()
    if "://" in str(output):
        raise GamedayError("a game day writes to a local folder, not to a remote target")
    if out == plan.data or plan.data in out.parents or out in plan.data.parents:
        raise GamedayError(f"the output folder {out} and the data folder {plan.data} overlap")
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise GamedayError(f"{out} exists and is not an empty folder: use a new one")
    # read every table and work out every injection before anything runs or is written
    loaded: list[tuple[Round, dict[str, Any], list[Injected]]] = []
    for rnd in plan.rounds:
        tables = {n: read_table(plan.data / n) for n in rnd.tables}
        _, injected = inject(tables, rnd.scenario, seed, root)
        loaded.append((rnd, tables, injected))
    report = Report(str(Path(plan_path)), str(plan.data), seed, dry_run=dry_run)
    if dry_run:
        for rnd, _, injected in loaded:
            report.rounds.append(
                RoundReport(rnd.name, rnd.inject, rnd.scenario, rnd.tables, injected)
            )
        return report
    out.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    for n, (rnd, _, _) in enumerate(loaded, start=1):
        folder = out / f"round-{n}"
        folder.mkdir()
        round_started = time.perf_counter()
        for name in rnd.tables:
            shutil.copy2(plan.data / name, folder / name)
        copies = {name: read_table(folder / name) for name in rnd.tables}
        mutated, injected = inject(copies, rnd.scenario, seed, root)
        for name, table in mutated.items():
            write_table(table, folder / name)
        rr = RoundReport(rnd.name, rnd.inject, rnd.scenario, rnd.tables, injected)
        values = {"{data}": str(plan.data), "{round}": str(folder), "{plan}": str(plan.path.parent)}
        found: set[str] = set()
        for command in rnd.checks:
            argv = _substitute(command, values)
            code, text, _, seconds = run_check(argv)
            rr.checks.append({"command": argv, "exit_code": code, "seconds": round(seconds, 3)})
            if argv[0] in results.SUPPORTED_COMMANDS and text.strip():
                try:
                    found |= results.fired(json.loads(text), argv[0])
                except (ValueError, results.ResultError):
                    pass  # the check failed before it printed a result: nothing detected
        rr.fired = sorted(found)
        rr.expectations = [
            {"expect": e, "detected": results.satisfied(e, found)} for e in rnd.expect
        ]
        rr.seconds = time.perf_counter() - round_started
        report.rounds.append(rr)
    report.seconds = time.perf_counter() - started
    (out / "gameday_report.json").write_text(
        json.dumps(report.to_dict(), indent=2) + "\n", encoding="utf-8"
    )
    (out / "gameday_report.md").write_text(report.markdown(), encoding="utf-8", newline="\n")
    return report
