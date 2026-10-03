"""``shape contracts``: validate consumer contracts and check them against a producer's profile.

See ``docs/CONSUMER_CONTRACTS.md``. Exit codes: 0 ok, 1 a consumer's contract is broken, 2
unusable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_DIR = Path("contracts") / "consumers"


def add_arguments(sub: Any) -> None:
    from shape.cli.project import add_project_flags

    p = sub.add_parser(
        "contracts",
        help="consumer data contracts: validate them, check them in the producer's CI",
    )
    acts = p.add_subparsers(dest="contracts_cmd", metavar="ACTION", required=True)
    v = acts.add_parser(
        "validate",
        help="check a consumer contract file and report every problem with its key path",
        description="Check FILE against the consumer contract format (shape-consumer-contract, "
        "version 1). Exit 0 when valid, 2 when not, with one line per problem.",
    )
    v.add_argument("file", metavar="FILE")
    v.add_argument("--json", action="store_true", help="print the result as JSON")
    c = acts.add_parser(
        "check-consumers",
        help="run every consumer contract of a source against a profile",
        description="Run the consumer contracts in DIR (default contracts/consumers/ next to "
        "shape.yml) whose `source` matches against PROFILE.shape, and report each consumer as "
        "pass or fail with its violations, its owner and the producer's column owners. With "
        "--baseline, also mark which violations are new: rules that passed on the baseline "
        "and fail on the profile. Exit 0 when every consumer passes, 1 when one fails, 2 for "
        "unusable input.",
    )
    c.add_argument("profile", metavar="PROFILE.shape")
    c.add_argument("--consumers", metavar="DIR", help="default: contracts/consumers/")
    c.add_argument("--baseline", metavar="BASE.shape", help="the profile before the change")
    c.add_argument(
        "--require-consumers",
        action="store_true",
        help="exit 2 when no consumer contract matches the source",
    )
    c.add_argument("-o", "--output", metavar="REPORT.json", help="write the report as JSON")
    c.add_argument("--json", action="store_true", help="print the report as JSON, not text")
    add_project_flags(c)


def _dump(obj: Any) -> None:
    print(json.dumps(obj, sort_keys=True, allow_nan=False))


def _validate(a: argparse.Namespace) -> int:
    from shape.consumers import ConsumerContractError, load

    try:
        c = load(a.file)
    except ConsumerContractError as exc:
        if a.json:
            _dump({"valid": False, "file": a.file, "problems": list(exc.problems)})
        else:
            for problem in exc.problems:
                print(f"shape: error: {a.file}: {problem}", file=sys.stderr)
        return 2
    _dump(
        {
            "valid": True,
            "file": a.file,
            "format": "shape-consumer-contract",
            "consumer": c.consumer,
            "source": c.source,
        }
    )
    return 0


def _content_id(path: str) -> str | None:
    from shape.artifact.io import read_manifest_bytes

    cid = json.loads(read_manifest_bytes(path)).get("shape_content_id")
    return str(cid) if cid else None


def _is_profile(path: str) -> bool:
    import zipfile

    from shape.artifact.io import read_manifest_bytes

    try:
        return bool(json.loads(read_manifest_bytes(path)).get("kind") == "profile")
    except (OSError, ValueError, KeyError, AttributeError, zipfile.BadZipFile, RecursionError):
        return False


def _profile(path: str) -> Any:
    import shape

    if not _is_profile(path):
        raise ValueError(f"{path} is not a .shape profile (write one with `shape profile`)")
    return shape.load(path)


def _resolve_source(a: argparse.Namespace, profile_name: str) -> tuple[Any, str, Path | None]:
    """``(source or None, source name, folder of shape.yml or None)``."""
    from shape.project import find_project, load_project

    named = a.source
    project = None
    if a.no_project:
        if a.project:
            raise ValueError("--no-project cannot be combined with --project")
    else:
        path = Path(a.project) if a.project else find_project()
        if path is not None:
            project = load_project(path)
    if project is None:
        if not named:
            raise ValueError(
                "which source? pass --source NAME (no shape.yml was found from here upwards)"
            )
        return None, named, None
    if named:
        return project.source(named), named, project.root
    if len(project.sources) == 1:
        (only,) = project.sources.values()
        return only, only.name, project.root
    if profile_name in project.sources:
        return project.sources[profile_name], profile_name, project.root
    raise ValueError(
        f"{project.path} defines {len(project.sources)} sources: pass --source NAME "
        f"({', '.join(sorted(project.sources))})"
    )


def _owner_lookup(source: Any) -> Any:
    """``(table, column) -> owner`` from the source's column settings in shape.yml."""
    if source is None:
        return lambda table, column: None
    return lambda table, column: source.owner_of(column, table or None) if column else None


def _check_consumers(a: argparse.Namespace) -> int:
    from shape.consumers import (
        ConsumerContractError,
        build_report,
        check_consumers,
        find_files,
        load_all,
        render_text,
    )

    profile = _profile(a.profile)
    baseline = _profile(a.baseline) if a.baseline else None
    source, source_name, root = _resolve_source(a, profile.name)
    directory = Path(a.consumers) if a.consumers else (root or Path.cwd()) / DEFAULT_DIR
    if not directory.is_dir():
        if a.consumers or a.require_consumers:
            raise ConsumerContractError(f"{directory}: no such folder of consumer contracts")
        files: list[Path] = []
    else:
        files = find_files(directory)
    try:
        contracts = [c for c in load_all(files) if c.source == source_name]
    except ConsumerContractError as exc:
        for problem in exc.problems:
            print(f"shape: error: {problem}", file=sys.stderr)
        return 2
    if a.require_consumers and not contracts:
        raise ConsumerContractError(
            f"no consumer contract for source {source_name!r} in {directory}"
        )
    results = check_consumers(profile, contracts, baseline=baseline, owner_of=_owner_lookup(source))
    info = {"name": profile.name, "path": a.profile, "content_id": _content_id(a.profile)}
    binfo = (
        {"name": baseline.name, "path": a.baseline, "content_id": _content_id(a.baseline)}
        if baseline is not None
        else None
    )
    report = build_report(source_name, info, results, baseline_info=binfo, directory=str(directory))
    if a.output:
        from shape.registry.profiles import atomic_write_text

        atomic_write_text(Path(a.output), json.dumps(report, indent=2, allow_nan=False) + "\n")
    if a.json:
        _dump(report)
    else:
        sys.stdout.write(render_text(report))
    return 0 if report["passed"] else 1


def run(a: argparse.Namespace) -> int:
    if a.contracts_cmd == "validate":
        return _validate(a)
    return _check_consumers(a)
