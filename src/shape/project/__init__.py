"""The Shape project file, ``shape.yml`` (W1-04): sources, baselines, thresholds, gates, owners.

``load_project`` reads and validates one; ``find_project`` looks for it from a folder upwards;
``resolve_baseline`` turns a source's baseline into profile files from the registry;
``scaffold`` writes a new project (``shape init``). YAML is read with the optional PyYAML
(extra ``yaml``); nothing here is imported until a command needs it.
"""

from __future__ import annotations

from shape.project.baseline import BaselineEntry, ResolvedBaseline, resolve_baseline
from shape.project.file import (
    FORMAT,
    VERSION,
    Baseline,
    ColumnSettings,
    Project,
    ProjectError,
    ProjectVersionError,
    Source,
    find_project,
    load_project,
    parse_project,
    problems,
    schema,
)

__all__ = [
    "FORMAT",
    "VERSION",
    "Baseline",
    "BaselineEntry",
    "ColumnSettings",
    "Project",
    "ProjectError",
    "ProjectVersionError",
    "ResolvedBaseline",
    "Source",
    "find_project",
    "load_project",
    "parse_project",
    "problems",
    "resolve_baseline",
    "schema",
]
