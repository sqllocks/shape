"""``shape init``, ``shape project validate``, and how the other commands read ``shape.yml``.

``shape profile``, ``diff``, ``check`` and ``verify`` look for ``shape.yml`` from the working
folder upwards (never above a repository root). ``--project FILE`` names one, ``--no-project``
ignores it. A flag on the command line always beats the file.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from shape.project import Project, ProjectError, Source


@dataclass(slots=True)
class Context:
    """The project a command runs under, and the source its settings come from (or None)."""

    project: Project
    source: Source | None = None

    def block(self) -> dict[str, Any]:
        """The ``project`` block of a command's JSON result."""
        out: dict[str, Any] = {
            "file": str(self.project.path),
            "format": "shape-project",
            "version": self.project.version,
        }
        if self.source is not None:
            out["source"] = self.source.name
        return out


def add_project_flags(parser: argparse.ArgumentParser, *, source: bool = True) -> None:
    g = parser.add_argument_group("project file (shape.yml)")
    g.add_argument("--project", metavar="FILE", help="use this project file instead of searching")
    g.add_argument("--no-project", action="store_true", help="ignore shape.yml")
    if source:
        g.add_argument(
            "--source", metavar="NAME", help="the source of shape.yml whose settings apply"
        )


def add_arguments(sub: Any) -> None:
    i = sub.add_parser(
        "init",
        help="scaffold a Shape project: shape.yml, folders, .gitattributes, a CI workflow",
        description="Write shape.yml, the folders data/, shapes/ and contracts/, a "
        ".gitattributes rule that makes .shape files diffable, and an example GitHub Actions "
        "workflow. An existing shape.yml is never overwritten (use --force); an existing "
        "workflow is kept; .gitattributes is only extended.",
    )
    i.add_argument("folder", metavar="DIR", nargs="?", default=".", help="default: this folder")
    i.add_argument("--name", metavar="NAME", help="the project's name (default: the folder's)")
    i.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="NAME[=PATH]",
        help="a source to declare (repeatable; PATH defaults to data/NAME; default: one "
        "source called example)",
    )
    i.add_argument("--force", action="store_true", help="replace shape.yml and the workflow")
    p = sub.add_parser("project", help="work with the project file (shape project validate)")
    acts = p.add_subparsers(dest="project_cmd", metavar="ACTION", required=True)
    v = acts.add_parser(
        "validate",
        help="check shape.yml and report every problem with its key path",
        description="Check shape.yml (found from here upwards, or FILE): the format and "
        "version, source names, thresholds, baselines and gates. Exit 0 when valid, 2 when "
        "not, with one line per problem.",
    )
    v.add_argument("file", metavar="FILE", nargs="?", help="default: the nearest shape.yml")
    v.add_argument("--json", action="store_true", help="print the result as JSON")


def _dump(obj: Any) -> None:
    print(json.dumps(obj, sort_keys=True, default=str))


def _load(path: str | os.PathLike[str]) -> Project:
    from shape.project import load_project

    return load_project(path)


def context(a: argparse.Namespace, hint: str | None = None) -> Context | None:
    """The project for a command, or None when there is none (or ``--no-project``). ``hint`` is
    the name of the thing being worked on (a profile's name), used to pick a source when the
    file has several and ``--source`` was not given."""
    from shape.project import find_project

    given = getattr(a, "project", None)
    named = getattr(a, "source", None)
    if getattr(a, "no_project", False):
        if given or named:
            raise ValueError("--no-project cannot be combined with --project or --source")
        return None
    path = Path(given) if given else find_project()
    if path is None:
        if named:
            raise ProjectError(f"no shape.yml found from {Path.cwd()} upwards: --source needs one")
        return None
    project = _load(path)
    if named:
        return Context(project, project.source(named))
    if len(project.sources) == 1:
        return Context(project, next(iter(project.sources.values())))
    if hint is not None and hint in project.sources:
        return Context(project, project.sources[hint])
    print(
        f"shape: note: {path} defines {len(project.sources)} sources and none was selected: "
        "its source settings were not applied (use --source NAME)",
        file=sys.stderr,
    )
    return Context(project, None)


def use_source_path(a: argparse.Namespace, field: str, ctx: Context | None) -> Source | None:
    """If ``a.<field>`` is not an existing path but names a source of the project, return that
    source (the caller then reads ``source.path``); an existing path always wins."""
    value = getattr(a, field)
    if ctx is None or os.path.exists(value) or "://" in value:
        return None
    return ctx.project.sources.get(value)


def baseline_date(a: argparse.Namespace) -> date | None:
    raw = getattr(a, "baseline_date", None)
    if raw is None:
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        raise ValueError(f"--baseline-date: invalid date {raw!r} (use YYYY-MM-DD)") from None


def merge_diff_options(
    source: Source | None, options: dict[str, Any], ignored_by_flag: bool
) -> dict[str, Any]:
    """The ``shape.diff`` keywords with the source's policy underneath the flags. An explicit
    ``--policy`` file replaces the project policy; ``--ignore`` replaces its ignore lists; the
    other flags override it key by key."""
    if source is None or options.get("policy") is not None:
        return options
    base = source.drift_policy()
    if ignored_by_flag:
        base.pop("ignore", None)
    return {**options, "policy": base or None}


def with_project_classes(config: Any, ctx: Context) -> Any:
    """The verify configuration with the change classes of ``shape.yml`` underneath it: the
    source's ``classes`` and the ``fail_on`` of the ``schema_drift`` gate, each used only when
    the configuration file does not set it."""
    from dataclasses import replace

    base: dict[str, Any] = {}
    if ctx.source is not None and ctx.source.classes:
        base["classes"] = dict(ctx.source.classes)
    fail_on = ctx.project.gate_fail_on("schema_drift")
    if fail_on is not None:
        base["fail_on"] = fail_on
    if not base:
        return config
    return replace(config, rules={**base, **config.rules})


def annotate(source: Source | None, record: dict[str, Any]) -> dict[str, Any]:
    """``record`` (a drift change or a contract violation) with the column's owner and
    annotations, when the project knows them."""
    if source is None:
        return record
    column = record.get("column")
    table = record.get("table")
    owner = source.owner_of(column, table) if isinstance(column, str) else None
    notes = source.annotations_of(column, table) if isinstance(column, str) else {}
    out = dict(record)
    if owner:
        out["owner"] = owner
    if notes:
        out["annotations"] = notes
    return out


def _init(a: argparse.Namespace) -> int:
    from shape.project.scaffold import scaffold

    result = scaffold(a.folder, name=a.name, sources=a.source, force=a.force)
    _dump(
        {
            "project": str(Path(a.folder) / "shape.yml"),
            "created": result.created,
            "updated": result.updated,
            "skipped": result.skipped,
        }
    )
    print(
        "next: put data under data/, run `shape project validate`, and `shape git-setup` in "
        "the repository to make `git diff` readable for .shape files",
        file=sys.stderr,
    )
    return 0


def _validate(a: argparse.Namespace) -> int:
    from shape.project import find_project

    path = Path(a.file) if a.file else find_project()
    if path is None:
        raise ProjectError(f"no shape.yml found from {Path.cwd()} upwards")
    try:
        project = _load(path)
    except ProjectError as exc:
        if a.json:
            _dump({"valid": False, "file": str(path), "problems": list(exc.problems)})
        else:
            for problem in exc.problems:
                print(f"shape: error: {path}: {problem}", file=sys.stderr)
        return 2
    _dump(
        {
            "valid": True,
            "file": str(path),
            "format": "shape-project",
            "version": project.version,
            "sources": sorted(project.sources),
            "gates": dict(project.gates),
        }
    )
    return 0


def run(a: argparse.Namespace) -> int:
    if a.cmd == "init":
        return _init(a)
    return _validate(a)


# ---- planned changes (W1-12) -------------------------------------------------------------------


def add_changes_flags(parser: argparse.ArgumentParser) -> None:
    g = parser.add_argument_group("planned changes (shape-changes.yml)")
    g.add_argument(
        "--changes",
        metavar="FILE",
        help="the planned-change file (default: the `changes` key of shape.yml, else "
        "shape-changes.yml next to it); see docs/PLANNED_CHANGES.md",
    )
    g.add_argument("--no-changes", action="store_true", help="ignore planned changes")
    g.add_argument(
        "--on",
        metavar="YYYY-MM-DD",
        help="the date planned changes are active on (default: today, UTC)",
    )


@dataclass(slots=True)
class Planned:
    """The planned changes a command runs under: the file, the day and the source."""

    plan: Any  # shape.project.changes.PlannedChanges
    on: date
    source: str | None

    def applier(self) -> Any:
        return self.plan.applier(self.on, self.source)

    def block(self) -> str:
        return str(self.plan.path)


def planned_for(a: argparse.Namespace, ctx: Context | None) -> Planned | None:
    """The planned changes for a command, or None (``--no-changes``, or no file). A named file
    that is missing, or any file that is not valid, is an input error."""
    from shape.project.changes import load_changes, parse_day, today

    raw_on = getattr(a, "on", None)
    on = parse_day(raw_on, "--on") if raw_on is not None else today()
    if getattr(a, "no_changes", False):
        if getattr(a, "changes", None):
            raise ValueError("--no-changes cannot be combined with --changes")
        return None
    named = getattr(a, "changes", None)
    path = Path(named) if named else (ctx.project.changes_file() if ctx else None)
    if path is None:
        return None
    plan = load_changes(path)
    return Planned(plan, on, ctx.source.name if ctx and ctx.source else None)


def expiry_notices(report: dict[str, Any]) -> None:
    """One warning line on stderr for each expired entry that would have matched."""
    for e in report.get("expired", ()):
        print(f"shape: warning: planned change {e['id']} expired on {e['until']}", file=sys.stderr)


def planned_summary(changes: list[dict[str, Any]]) -> None:
    """The planned changes of a result as text on stderr, each marked ``(planned: ID)``."""
    for c in changes:
        mark = c.get("planned")
        if mark:
            what = c.get("column") or "table"
            print(
                f"shape: {what}: {c.get('kind') or c.get('rule')} (planned: {mark['id']})",
                file=sys.stderr,
            )


def diff_summary(out: dict[str, Any]) -> None:
    """The text of ``shape diff`` on stderr: each change with its class (and the planned entry
    that covers it), then the bump (``bump: major (2 breaking, 1 additive, 4 cosmetic)``)."""
    for c in out["changes"]:
        what = c.get("column") or "table"
        mark = c.get("planned")
        planned = f" (planned: {mark['id']})" if mark else ""
        print(f"shape: {what}: {c['kind']} [{c['class']}]{planned}", file=sys.stderr)
    s = out["semver"]
    if "next_version" in s:
        print(f"version: {s['next_version']}", file=sys.stderr)
    print(
        f"bump: {s['bump']} ({s['breaking']} breaking, {s['additive']} additive, "
        f"{s['cosmetic']} cosmetic)",
        file=sys.stderr,
    )
