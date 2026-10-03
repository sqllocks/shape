"""Reading and validating ``shape.yml``.

The JSON Schema in ``shape/schemas/shape-project-v1.schema.json`` is the structural truth: it is
checked with :mod:`shape.schemacheck`, so the file and the code cannot drift apart. What a schema
cannot say (name syntax, non-empty strings, which baseline keys go with which kind, gate names)
is checked here. Every problem is reported at once, each with its key path.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Any

from shape.errors import ShapeError
from shape.schemacheck import validate

FORMAT = "shape-project"
VERSION = 1
FILE_NAMES = ("shape.yml", "shape.yaml")
MAX_BYTES = 1 << 20

_PLACEHOLDER = re.compile(r"\{command\}")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
#: ``shape profile NAME`` words that are subcommands, so a source cannot carry them.
RESERVED_SOURCE_NAMES = frozenset({"safe", "validate", "export", "import", "list", "registry"})
BASELINE_KINDS = ("previous_run", "same_weekday", "rolling_window", "month_end", "pinned")
GATE_MODES = ("observe", "enforce")
DEFAULT_REGISTRY = "shapes/registry"


class ProjectError(ShapeError, ValueError):
    """``shape.yml`` cannot be read, is not valid, or names something that does not exist.
    ``problems`` holds every problem found (without the file name)."""

    def __init__(self, message: str, problems: tuple[str, ...] = ()) -> None:
        super().__init__(message)
        self.problems = problems or (message,)


class ProjectVersionError(ProjectError):
    """The file was written for a newer version of the format than this Shape understands."""


@cache
def schema() -> dict[str, Any]:
    """The JSON Schema of ``shape.yml``, as shipped in ``shape/schemas``."""
    text = (
        resources.files("shape").joinpath("schemas/shape-project-v1.schema.json").read_text("utf-8")
    )
    loaded: dict[str, Any] = json.loads(text)
    return loaded


# ---- the model ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Baseline:
    kind: str
    registry: str
    name: str
    window: int | None = None
    artifact: str | None = None
    ref: str | None = None


@dataclass(frozen=True, slots=True)
class ColumnSettings:
    thresholds: Mapping[str, Any] = field(default_factory=dict)
    ignore: bool = False
    owner: str | None = None
    annotations: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Source:
    name: str
    path: str
    dataset: bool = False
    contract: str | None = None
    baseline: Baseline | None = None
    thresholds: Mapping[str, Any] = field(default_factory=dict)
    ignore: tuple[str, ...] = ()
    columns: Mapping[str, ColumnSettings] = field(default_factory=dict)

    def drift_policy(self) -> dict[str, Any]:
        """The ``shape diff`` policy of this source (the layout of ``--policy``)."""
        policy: dict[str, Any] = {}
        if self.thresholds:
            policy["thresholds"] = dict(self.thresholds)
        per_column = {c: dict(s.thresholds) for c, s in self.columns.items() if s.thresholds}
        if per_column:
            policy["columns"] = per_column
        ignore = [*self.ignore, *(c for c, s in self.columns.items() if s.ignore)]
        if ignore:
            policy["ignore"] = ignore
        return policy

    def settings_for(self, table: str | None, column: str | None) -> ColumnSettings | None:
        """The settings of a column: ``table.column``, then the name, then a matching glob."""
        if not column or not self.columns:
            return None
        full = f"{table}.{column}" if table else None
        for key in (full, column):
            if key is not None and key in self.columns:
                return self.columns[key]
        for pattern in sorted(self.columns):
            names = [n for n in (column, full) if n]
            if any(fnmatch.fnmatchcase(n, pattern) for n in names):
                return self.columns[pattern]
        return None

    def owner_of(self, column: str, table: str | None = None) -> str | None:
        found = self.settings_for(table, column)
        return found.owner if found else None

    def annotations_of(self, column: str, table: str | None = None) -> dict[str, Any]:
        found = self.settings_for(table, column)
        return dict(found.annotations) if found else {}


@dataclass(frozen=True, slots=True)
class Project:
    path: Path
    document: Mapping[str, Any]
    name: str | None
    sources: Mapping[str, Source]
    gates: Mapping[str, str]

    @property
    def ci(self) -> Mapping[str, str]:
        """The ``ci:`` defaults (``junit``, ``sarif``, ``json`` report paths); empty when absent."""
        found = self.document.get("ci")
        return dict(found) if isinstance(found, dict) else {}

    @property
    def root(self) -> Path:
        return self.path.parent

    @property
    def version(self) -> int:
        return int(self.document["version"])

    def source(self, name: str) -> Source:
        try:
            return self.sources[name]
        except KeyError:
            known = ", ".join(sorted(self.sources)) or "none"
            raise ProjectError(f"no source {name!r} in {self.path} (sources: {known})") from None

    def gate_mode(self, gate: str) -> str:
        """``observe`` or ``enforce``; a gate the file does not list is enforced."""
        return self.gates.get(gate, "enforce")


# ---- discovery and parsing ---------------------------------------------------------------------


def find_project(start: str | os.PathLike[str] | None = None) -> Path | None:
    """The ``shape.yml`` (or ``shape.yaml``) in ``start`` or the nearest folder above it, never
    looking above a repository root (a folder with ``.git``)."""
    here = Path(start if start is not None else Path.cwd()).resolve()
    for folder in (here, *here.parents):
        for name in FILE_NAMES:
            if (folder / name).is_file():
                return folder / name
        if (folder / ".git").exists():
            return None
    return None


def _refuse_duplicate_keys(text: str) -> None:
    """YAML keeps the last of two equal keys without a word; a project file refuses them."""
    import yaml  # type: ignore[import-untyped]

    root = yaml.compose(text, Loader=yaml.SafeLoader)
    seen_nodes: set[int] = set()
    stack = [root] if root is not None else []
    while stack:
        node = stack.pop()
        if id(node) in seen_nodes:
            continue
        seen_nodes.add(id(node))
        if isinstance(node, yaml.SequenceNode):
            stack.extend(node.value)
        elif isinstance(node, yaml.MappingNode):
            keys: set[tuple[str, str]] = set()
            for key_node, value_node in node.value:
                if isinstance(key_node, yaml.ScalarNode):
                    ident = (key_node.tag, key_node.value)
                    if ident in keys:
                        line = key_node.start_mark.line + 1
                        raise ProjectError(f"duplicate key {key_node.value!r} at line {line}")
                    keys.add(ident)
                stack.append(value_node)


def _read_yaml(text: str) -> Any:
    try:
        import yaml
    except ImportError:
        raise ProjectError(
            'reading shape.yml needs PyYAML: pip install "sqllocks-shape[yaml]"'
        ) from None
    from shape.security.yamlsafe import safe_load_yaml

    try:
        doc = safe_load_yaml(text)  # bounded and safe: no Python objects, no alias bombs
        _refuse_duplicate_keys(text)
        return doc
    except yaml.MarkedYAMLError as exc:
        mark = exc.problem_mark
        where = f"line {mark.line + 1}, column {mark.column + 1}: " if mark else ""
        raise ProjectError(f"invalid YAML at {where}{exc.problem}") from None
    except yaml.YAMLError as exc:
        raise ProjectError(f"invalid YAML: {exc}") from None
    except ValueError as exc:
        raise ProjectError(str(exc)) from None


def _path_text(path: str) -> str:
    return path[2:] if path.startswith("$.") else ("document" if path == "$" else path)


def _tidy(line: str) -> str:
    """A schemacheck violation as ``key.path: message`` (``unknown threshold`` for a bad name)."""
    path, _, message = line.partition(": ")
    path = _path_text(path)
    found = re.fullmatch(r"unexpected key '(.*)'", message)
    if found and path.endswith("thresholds"):
        known = ", ".join(sorted(schema()["$defs"]["thresholds"]["properties"]))
        return f"{path}: unknown threshold '{found[1]}' (known: {known})"
    return f"{path}: {message}"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text_problems(where: str, value: Any, out: list[str]) -> None:
    if isinstance(value, str) and not value.strip():
        out.append(f"{where}: must not be empty")


def _baseline_problems(where: str, b: dict[str, Any], out: list[str]) -> None:
    kind = b.get("kind")
    for key in ("registry", "name", "artifact", "ref"):
        if key in b:
            _text_problems(f"{where}.{key}", b[key], out)
    if isinstance(b.get("name"), str) and b["name"].strip() and not _NAME.fullmatch(b["name"]):
        out.append(f"{where}.name: {b['name']!r} is not a registry name (letters, digits, . _ -)")
    if kind not in BASELINE_KINDS:
        return
    if kind == "rolling_window":
        if "window" not in b:
            out.append(f"{where}.window: window is required for rolling_window")
    elif "window" in b:
        out.append(f"{where}.window: window only applies to rolling_window")
    if kind == "pinned":
        if ("artifact" in b) == ("ref" in b):
            out.append(f"{where}: pinned needs exactly one of artifact or ref")
    else:
        for key in ("artifact", "ref"):
            if key in b:
                out.append(f"{where}.{key}: {key} only applies to pinned")


def _semantic_problems(doc: dict[str, Any]) -> list[str]:
    out: list[str] = []
    _text_problems("name", doc.get("name"), out)
    for sname, src in _dict(doc.get("sources")).items():
        where = f"sources.{sname}"
        if not _NAME.fullmatch(str(sname)):
            out.append(
                f"{where}: source name {sname!r} is not valid "
                "(letters, digits, . _ -; it starts with a letter or digit)"
            )
        elif sname in RESERVED_SOURCE_NAMES:
            out.append(f"{where}: source name {sname!r} is a `shape profile` subcommand")
        s = _dict(src)
        for key in ("path", "contract"):
            if key in s:
                _text_problems(f"{where}.{key}", s[key], out)
        if isinstance(s.get("baseline"), dict):
            _baseline_problems(f"{where}.baseline", s["baseline"], out)
        for i, item in enumerate(s["ignore"] if isinstance(s.get("ignore"), list) else []):
            _text_problems(f"{where}.ignore[{i}]", item, out)
        for cname, col in _dict(s.get("columns")).items():
            if not str(cname).strip():
                out.append(f"{where}.columns: a column name must not be empty")
            if "owner" in _dict(col):
                _text_problems(f"{where}.columns.{cname}.owner", col["owner"], out)
    for key, value in _dict(doc.get("ci")).items():
        _text_problems(f"ci.{key}", value, out)
        if isinstance(value, str) and set(_PLACEHOLDER.sub("", value)) & {"{", "}"}:
            out.append(f"ci.{key}: only {{command}} may appear in braces")
    if isinstance(doc.get("gates"), dict) and doc["gates"]:
        from shape.quality.gates import GateRunner

        known = GateRunner.available_gates()
        for gname in doc["gates"]:
            if gname not in known:
                out.append(f"gates: unknown gate {gname!r} (gates: {', '.join(known)})")
    return out


def problems(doc: Any) -> list[str]:
    """Every problem of a parsed document (empty when it is valid), each starting with the key
    path. A document of a newer version is reported as that and nothing else."""
    if not isinstance(doc, dict):
        return [f"document: must be a mapping (key: value pairs), got {type(doc).__name__}"]
    if doc.get("format") == FORMAT:
        version = doc.get("version")
        if isinstance(version, int) and not isinstance(version, bool) and version > VERSION:
            return [_newer(version)]
    found = [_tidy(line) for line in validate(doc, schema())]
    return found + _semantic_problems(doc)


def _newer(version: int) -> str:
    return (
        f"version {version} is newer than this Shape understands (it reads up to version "
        f"{VERSION}): upgrade Shape, or lower the file's version if it uses nothing newer"
    )


def _resolve(root: Path, value: str) -> str:
    """A path from the file: URIs and absolute paths stay, the rest goes under the file's folder."""
    if "://" in value or os.path.isabs(value):
        return value
    return str(root / value)


def _source(root: Path, name: str, doc: dict[str, Any]) -> Source:
    raw = doc.get("baseline")
    baseline = None
    if raw is not None:
        baseline = Baseline(
            kind=raw["kind"],
            registry=_resolve(root, raw.get("registry", DEFAULT_REGISTRY)),
            name=raw.get("name", name),
            window=raw.get("window"),
            artifact=_resolve(root, raw["artifact"]) if "artifact" in raw else None,
            ref=raw.get("ref"),
        )
    columns = {
        str(c): ColumnSettings(
            thresholds=dict(v.get("thresholds", {})),
            ignore=bool(v.get("ignore", False)),
            owner=v.get("owner"),
            annotations=dict(v.get("annotations", {})),
        )
        for c, v in doc.get("columns", {}).items()
    }
    return Source(
        name=name,
        path=_resolve(root, doc["path"]),
        dataset=bool(doc.get("dataset", False)),
        contract=_resolve(root, doc["contract"]) if "contract" in doc else None,
        baseline=baseline,
        thresholds=dict(doc.get("thresholds", {})),
        ignore=tuple(doc.get("ignore", ())),
        columns=columns,
    )


def parse_project(text: str, path: str | os.PathLike[str]) -> Project:
    """Parse and validate the text of a project file that lives at ``path`` (which decides what
    relative paths in it mean). Raises :class:`ProjectError`."""
    where = Path(path)
    try:
        if len(text.encode("utf-8")) > MAX_BYTES:
            raise ProjectError("the file is larger than 1 MiB")
        if not text.strip():
            raise ProjectError("the file is empty")
        doc = _read_yaml(text)
        found = problems(doc)
        if found and isinstance(doc, dict) and doc.get("format") == FORMAT:
            version = doc.get("version")
            if isinstance(version, int) and not isinstance(version, bool) and version > VERSION:
                raise ProjectVersionError(_newer(version))
    except ProjectVersionError as exc:
        raise ProjectVersionError(f"{where}: {exc}", exc.problems) from None
    except ProjectError as exc:
        raise ProjectError(f"{where}: {exc}", exc.problems) from None
    if found:
        raise ProjectError(f"{where}: " + "; ".join(found), tuple(found))
    sources = {str(n): _source(where.parent, str(n), s) for n, s in doc["sources"].items()}
    gates = {str(g): v["mode"] for g, v in doc.get("gates", {}).items()}
    return Project(where, doc, doc.get("name"), sources, gates)


def load_project(path: str | os.PathLike[str]) -> Project:
    """Read ``shape.yml`` from ``path``. Raises ``FileNotFoundError`` and :class:`ProjectError`."""
    p = Path(path)
    if p.stat().st_size > MAX_BYTES:
        raise ProjectError(
            f"{p}: the file is larger than 1 MiB", ("the file is larger than 1 MiB",)
        )
    try:
        text = p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ProjectError(f"{p}: not a UTF-8 text file", ("not a UTF-8 text file",)) from None
    return parse_project(text, p)
