"""``shape init``: the files of a new Shape project."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from shape.project.file import _NAME, FILE_NAMES, RESERVED_SOURCE_NAMES, ProjectError

WORKFLOW = Path(".github") / "workflows" / "shape.yml"
FOLDERS = ("data", "shapes", "contracts", "contracts/consumers")
DEFAULT_SOURCE = "example"

_PROJECT = """\
# The Shape project file. Reference: docs/PROJECT.md (JSON Schema: shape-project-v1.schema.json).
# `shape project validate` checks it; flags on the command line override what is set here.
format: shape-project
version: 1
name: {name}

sources:
{sources}
# Validation gates and their modes. A gate that is not listed is enforced.
# observe: `shape verify` reports the gate but its failure does not change the exit code.
gates:
  schema_conformance:
    mode: observe
  # referential_integrity:
  #   mode: enforce
"""

_SOURCE = """\
  {name}:
    path: {path}
    # Seed the baseline once: shape registry shapes/registry commit {name} FILE.shape --allow-raw
    baseline:
      kind: previous_run         # or same_weekday, rolling_window, month_end, pinned
      registry: shapes/registry
    # thresholds:                 # drift thresholds for the whole source (docs/DRIFT.md)
    #   null_rate: 0.02
    # ignore: [load_ts]           # columns left out of the comparison
    # columns:
    #   amount:
    #     thresholds:
    #       mean_shift_std: 1.0
    #     owner: finance-data@example.com
    #     annotations:
    #       unit: EUR
"""

_WORKFLOW = """\
# Shape checks for this project (created by `shape init`; Shape never rewrites it).
# Consumer teams commit their contracts to contracts/consumers/ (docs/CONSUMER_CONTRACTS.md).
# Seed each source's baseline once, then commit shapes/registry:
#   shape profile SOURCE -o shapes/current/SOURCE.shape
#   shape registry shapes/registry commit SOURCE shapes/current/SOURCE.shape --allow-raw
name: shape
on:
  pull_request:
  push:
    branches: [main]
  schedule:
    - cron: "17 5 * * *"
jobs:
  shape:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install Shape
        run: pip install "sqllocks-shape[yaml]"
      - name: Validate shape.yml
        run: shape project validate
{steps}      - name: Keep the drift reports
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: shape-reports
          path: shapes/current
          if-no-files-found: ignore
"""

_STEPS = """\
      - name: Profile {name}
        run: |
          mkdir -p shapes/current
          shape profile {name} -o shapes/current/{name}.shape
      - name: Check the consumers of {name}
        run: |
          shape contracts check-consumers shapes/current/{name}.shape --source {name} \\
            -o shapes/current/{name}.consumers.json
      - name: Compare {name} with its baseline
        if: ${{{{ hashFiles('shapes/registry/logs/{name}.jsonl') != '' }}}}
        run: |
          shape diff --source {name} shapes/current/{name}.shape --fail-on-drift \\
            --json shapes/current/{name}.diff.json
"""


def parse_sources(specs: list[str]) -> list[tuple[str, str]]:
    """``NAME`` or ``NAME=PATH`` items of ``--source``; the default is one source, ``example``."""
    out: list[tuple[str, str]] = []
    for spec in specs or [DEFAULT_SOURCE]:
        name, eq, path = spec.partition("=")
        if not name or (eq and not path):
            raise ValueError(f"--source expects NAME or NAME=PATH, got {spec!r}")
        if not _NAME.fullmatch(name):
            raise ValueError(
                f"--source {name!r} is not a valid source name (letters, digits, . _ -; "
                "it starts with a letter or digit)"
            )
        if name in RESERVED_SOURCE_NAMES:
            raise ValueError(f"--source {name!r} is a `shape profile` subcommand")
        if any(name == n for n, _ in out):
            raise ValueError(f"--source {name!r} was given twice")
        out.append((name, path or f"data/{name}"))
    return out


_PLAIN = re.compile(r"[A-Za-z_./][A-Za-z0-9_./-]*")
_YAML_WORDS = frozenset({"true", "false", "yes", "no", "on", "off", "null", "y", "n"})


def _yaml_scalar(value: str) -> str:
    """``value`` as a YAML scalar: plain when that is safe, else a JSON (double-quoted) string."""
    if _PLAIN.fullmatch(value) and value.lower() not in _YAML_WORDS and _reads_back(value):
        return value
    return json.dumps(value)


def _reads_back(value: str) -> bool:
    """Whether YAML reads the plain scalar ``value`` as that same string (``.inf``, ``.5`` and
    ``.nan`` are numbers)."""
    try:
        import yaml  # type: ignore[import-untyped]

        return bool(yaml.safe_load(value) == value)
    except Exception:  # not installed or not parseable: quote it
        return False


@dataclass(slots=True)
class ScaffoldResult:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _write(target: Path, text: str) -> None:
    from shape.registry.profiles import atomic_write_text

    atomic_write_text(target, text, newline="\n")


def scaffold(
    folder: str | os.PathLike[str],
    *,
    name: str | None = None,
    sources: list[str] | None = None,
    force: bool = False,
) -> ScaffoldResult:
    """Write a project into ``folder`` (created when missing). Nothing is written when a
    precondition fails; ``shape.yml`` is never replaced without ``force``, an existing workflow is
    kept (without ``force``), and ``.gitattributes`` is only ever extended."""
    root = Path(folder)
    pairs = parse_sources(sources or [])
    label = name if name is not None else (root.resolve().name or DEFAULT_SOURCE)
    if not label.strip():
        raise ValueError("--name must not be empty")
    existing = [n for n in FILE_NAMES if (root / n).exists()]
    if existing and not force:
        raise ProjectError(
            f"{root / existing[0]} already exists: init never overwrites a project file "
            "(use --force to replace it)"
        )
    if root.exists() and not root.is_dir():
        raise ValueError(f"{root} is not a folder")
    for rel in (*FILE_NAMES[:1], ".gitattributes", str(WORKFLOW)):
        if (root / rel).is_symlink():
            raise ValueError(f"{root / rel} is a symbolic link; refusing to write through it")

    folders = [root / f for f in FOLDERS] + [root / ".github", root / ".github" / "workflows"]
    files = [root / f / ".gitkeep" for f in FOLDERS] + [root / WORKFLOW]
    for path, want_folder in [(p, True) for p in folders] + [(p, False) for p in files]:
        if path.exists() and path.is_dir() != want_folder:
            raise ValueError(f"{path} exists and is not a {'folder' if want_folder else 'file'}")
    attrs = root / ".gitattributes"
    try:
        attr_lines = attrs.read_text(encoding="utf-8").splitlines() if attrs.exists() else []
    except (UnicodeDecodeError, OSError) as exc:
        raise ValueError(f"{attrs} cannot be read as UTF-8 text ({exc})") from None

    result = ScaffoldResult()
    text = _PROJECT.format(
        name=_yaml_scalar(label),
        sources="".join(_SOURCE.format(name=n, path=_yaml_scalar(p)) for n, p in pairs),
    )
    root.mkdir(parents=True, exist_ok=True)
    project_file = root / FILE_NAMES[0]
    (result.updated if project_file.exists() else result.created).append(FILE_NAMES[0])
    _write(project_file, text)

    for folder_name in FOLDERS:
        keep = root / folder_name / ".gitkeep"
        rel = f"{folder_name}/.gitkeep"
        if keep.exists():
            result.skipped.append(rel)
            continue
        keep.parent.mkdir(parents=True, exist_ok=True)
        keep.write_text("", encoding="utf-8", newline="\n")
        result.created.append(rel)

    from shape.cli.gitcmds import DEFAULT_PATTERN, _has_rule

    rule = f"{DEFAULT_PATTERN} diff=shape"
    lines = attr_lines
    if not _has_rule(lines, DEFAULT_PATTERN):
        (result.updated if attrs.exists() else result.created).append(".gitattributes")
        _write(attrs, "\n".join([*lines, rule]) + "\n")

    flow = root / WORKFLOW
    rel_flow = WORKFLOW.as_posix()
    if flow.exists() and not force:
        result.skipped.append(rel_flow)
    else:
        (result.updated if flow.exists() else result.created).append(rel_flow)
        steps = "".join(_STEPS.format(name=n) for n, _ in pairs)
        _write(flow, _WORKFLOW.format(steps=steps))
    return result
