"""The Fabric platform inventory (``docs/FABRIC_PLATFORM.md``) and its guard (W7-01).

``parse_table`` reads the one inventory table. ``scan_python`` finds, with ``ast``, the Fabric
REST paths, OneLake endpoints and item types a Python file builds. ``scan_runtimes`` finds the
Fabric Spark runtime versions a Markdown file names. ``check`` compares what was found with the
table and returns one message per problem, each naming file, line and surface.

A surface in the table matches a found one when the paths are equal after every ``{parameter}``
is reduced to ``{}`` and, when the call names its HTTP method, the methods are equal.
"""

from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY = ROOT / "docs" / "FABRIC_PLATFORM.md"

KINDS = ("rest", "item-type", "runtime", "storage")
STATUSES = ("GA", "preview", "retired")
COLUMNS = ("Kind", "Surface", "Status", "End of support", "Source", "Checked", "Used by")
NONE_ANNOUNCED = "none announced"
NOT_APPLICABLE = "n/a"
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")

FABRIC_API_BASE = "https://api.fabric.microsoft.com/v1"
ONELAKE_BASE = "https://onelake.dfs.fabric.microsoft.com"
# Names the code uses for those two base URLs (``shape.scale.http``).
CONSTANTS = {"FABRIC_API": FABRIC_API_BASE, "ONELAKE_DFS": ONELAKE_BASE}
ONELAKE_SURFACE = ONELAKE_BASE

PY_GLOBS = (
    "plugins/shape-fabric/src/**/*.py",
    "src/shape/scale/**/*.py",
    "plugins/shape-fabric/tests/test_live*.py",
)
RUNTIME_GLOBS = ("docs/**/*.md", "plugins/*/README.md")
RUNTIME_SKIP = ("docs/plans/", "docs/FABRIC_PLATFORM.md")


class TableError(ValueError):
    """The inventory table is malformed."""


@dataclass(frozen=True)
class Row:
    kind: str
    surface: str
    status: str
    end_of_support: str
    source: str
    checked: date
    used_by: str
    line: int

    @property
    def key(self) -> str:
        return surface_key(self.kind, self.surface)


@dataclass(frozen=True)
class Found:
    file: str
    line: int
    kind: str
    surface: str  # "METHOD /v1/path/{param}", "/v1/path" when the call does not name its method
    method: str | None = None
    path: str = ""

    @property
    def key(self) -> str:
        return surface_key(self.kind, self.surface)


def reduce_params(path: str) -> str:
    return re.sub(r"\{[^{}]*\}", "{}", path)


def surface_key(kind: str, surface: str) -> str:
    """``kind`` and ``surface`` reduced to what must be equal for the two to be the same."""
    if kind == "rest":
        method, _, path = surface.partition(" ")
        if method in METHODS:
            return f"rest {method} {reduce_params(path)}"
        return f"rest {reduce_params(surface)}"
    return f"{kind} {surface.strip()}"


# ---- the table ---------------------------------------------------------------------------------


def _iso(value: str, what: str, line: int) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise TableError(f"line {line}: {what} {value!r} is not an ISO date (YYYY-MM-DD)") from None


def parse_table(text: str) -> list[Row]:
    """The rows of the inventory table (the first table whose header is :data:`COLUMNS`)."""
    lines = text.splitlines()
    start = None
    for i, raw in enumerate(lines):
        cells = _cells(raw)
        if cells and tuple(cells) == COLUMNS:
            start = i
            break
        if cells and cells[0] == COLUMNS[0]:
            raise TableError(
                f"line {i + 1}: the table header must be exactly: | " + " | ".join(COLUMNS) + " |"
            )
    if start is None:
        raise TableError("no inventory table: expected a header | " + " | ".join(COLUMNS) + " |")
    rows: list[Row] = []
    seen: dict[str, int] = {}
    for i in range(start + 2, len(lines)):
        raw = lines[i]
        if not raw.lstrip().startswith("|"):
            break
        cells = _cells(raw)
        n = i + 1
        if len(cells) != len(COLUMNS):
            raise TableError(f"line {n}: {len(cells)} columns, expected {len(COLUMNS)}")
        kind, surface, status, eos, source, checked, used_by = cells
        surface = surface.strip("`")
        if kind not in KINDS:
            raise TableError(f"line {n}: Kind {kind!r} is not one of {', '.join(KINDS)}")
        if status not in STATUSES:
            raise TableError(f"line {n}: Status {status!r} is not one of {', '.join(STATUSES)}")
        if not surface:
            raise TableError(f"line {n}: empty Surface")
        if kind == "rest":
            method, _, path = surface.partition(" ")
            if method not in METHODS or not path.startswith("/v1/"):
                raise TableError(f"line {n}: a rest Surface is 'METHOD /v1/path', got {surface!r}")
        if kind == "runtime":
            if eos != NONE_ANNOUNCED:
                _iso(eos, "End of support", n)
        elif eos != NOT_APPLICABLE:
            raise TableError(
                f"line {n}: End of support is for runtimes only; write {NOT_APPLICABLE}"
            )
        if not source.startswith("https://learn.microsoft.com/"):
            raise TableError(f"line {n}: Source must be a Microsoft Learn page, got {source!r}")
        if not used_by:
            raise TableError(f"line {n}: empty Used by")
        row = Row(kind, surface, status, eos, source, _iso(checked, "Checked", n), used_by, n)
        if row.key in seen:
            raise TableError(f"line {n}: {surface!r} is already listed on line {seen[row.key]}")
        seen[row.key] = n
        rows.append(row)
    if not rows:
        raise TableError("the inventory table has no rows")
    return rows


def _cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return []
    return [c.strip() for c in stripped.strip("|").split("|")]


# ---- Python files ------------------------------------------------------------------------------


def _name_of(node: ast.expr) -> str:
    """The name a ``{...}`` placeholder gets: the last identifier of the expression, looking
    inside a call (``quote(self.target.database)`` is ``database``)."""
    if isinstance(node, ast.Name):
        return node.id.lstrip("_")
    if isinstance(node, ast.Attribute):
        return node.attr.lstrip("_")
    if isinstance(node, ast.Call) and node.args:
        return _name_of(node.args[0])
    if isinstance(node, ast.Subscript):
        return _name_of(node.value)
    return "param"


def _render(node: ast.expr) -> str | None:
    """A string literal, an f-string or ``CONSTANT + "..."`` as text with ``{name}`` for the
    parts that are computed; ``None`` for anything else."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        out = []
        for part in node.values:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                if isinstance(part.value, ast.Name) and part.value.id in CONSTANTS:
                    out.append(CONSTANTS[part.value.id])
                else:
                    out.append("{" + _name_of(part.value) + "}")
        return "".join(out)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _render(node.left), _render(node.right)
        if isinstance(node.left, ast.Name) and node.left.id in CONSTANTS:
            left = CONSTANTS[node.left.id]
        if left is not None and right is not None:
            return left + right
    return None


def _call_method(call: ast.Call) -> str | None:
    for arg in call.args[:1]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and arg.value in METHODS:
            return arg.value
    for kw in call.keywords:
        if kw.arg == "method" and isinstance(kw.value, ast.Constant):
            value = kw.value.value
            return value if isinstance(value, str) and value in METHODS else None
    return None


_KUSTO_PATH = re.compile(r"^(?:https?://[^/\s]+|\{[^{}]*\})?(/v1/rest/[^\s?#`'\"]*)")


def _rest_path(text: str) -> str | None:
    """The ``/v1/...`` path of a Fabric or Kusto URL that ``text`` is, or ``None``."""
    if text.startswith(FABRIC_API_BASE):
        path = "/v1" + text[len(FABRIC_API_BASE) :]
    else:
        m = _KUSTO_PATH.match(text)
        if m is None:
            return None
        path = m.group(1)
    path = re.split(r"[?#]", path, maxsplit=1)[0].rstrip("/")
    return None if path == "/v1" else path  # the base URL constant itself


def _item_types_in_query(text: str) -> list[str]:
    return [m for m in re.findall(r"[?&]type=([A-Za-z][A-Za-z0-9]*)", text)]


def scan_source(source: str, label: str) -> list[Found]:
    """Every Fabric surface that the Python ``source`` builds (``label`` names the file)."""
    tree = ast.parse(source, filename=label)
    parents: dict[int, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[id(child)] = node
    found: list[Found] = []
    docstrings = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    }
    inner: set[int] = set()  # pieces of an f-string or a "+" chain are read with their parent
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr | ast.BinOp):
            for child in ast.walk(node):
                if child is not node:
                    inner.add(id(child))
    for node in ast.walk(tree):
        if id(node) in inner or id(node) in docstrings:
            continue
        if isinstance(node, ast.Dict):
            keys = {
                k.value: v
                for k, v in zip(node.keys, node.values, strict=True)
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
            kind_node = keys.get("type")
            if (
                "displayName" in keys
                and isinstance(kind_node, ast.Constant)
                and isinstance(kind_node.value, str)
            ):
                found.append(Found(label, kind_node.lineno, "item-type", kind_node.value))
            continue
        if not isinstance(node, ast.Constant | ast.JoinedStr | ast.BinOp):
            continue
        text = _render(node)
        if text is None:
            continue
        line = node.lineno
        for item_type in _item_types_in_query(text):
            found.append(Found(label, line, "item-type", item_type))
        path = _rest_path(text)
        if path is not None:
            call = parents.get(id(node))
            method = _call_method(call) if isinstance(call, ast.Call) else None
            surface = f"{method} {path}" if method else path
            found.append(Found(label, line, "rest", surface, method, path))
        if text.startswith(ONELAKE_BASE):
            found.append(Found(label, line, "storage", ONELAKE_SURFACE))
    return found


def scan_file(path: Path, root: Path = ROOT) -> list[Found]:
    try:
        label = path.relative_to(root).as_posix()
    except ValueError:
        label = path.as_posix()
    return scan_source(path.read_text(encoding="utf-8"), label)


def python_files(root: Path = ROOT, globs: Iterable[str] = PY_GLOBS) -> list[Path]:
    files: set[Path] = set()
    for pattern in globs:
        files.update(p for p in root.glob(pattern) if p.is_file() and "__pycache__" not in p.parts)
    return sorted(files)


# ---- runtimes ----------------------------------------------------------------------------------

_RUNTIME = re.compile(r"(?:(Fabric)\s+)?([Rr])untime\s+(\d+\.\d+)\b")


def scan_runtimes(text: str, label: str) -> list[Found]:
    """The Fabric Spark runtime versions named in Markdown ``text``: ``Runtime 2.0``, or
    ``Fabric runtime 1.3`` (a bare lower-case "runtime 3.11" is a language runtime)."""
    found = []
    for n, line in enumerate(text.splitlines(), 1):
        for m in _RUNTIME.finditer(line):
            if m.group(1) or m.group(2) == "R":
                found.append(Found(label, n, "runtime", m.group(3)))
    return found


def runtime_files(root: Path = ROOT) -> list[Path]:
    files: set[Path] = set()
    for pattern in RUNTIME_GLOBS:
        for p in root.glob(pattern):
            rel = p.relative_to(root).as_posix()
            if p.is_file() and not rel.startswith(RUNTIME_SKIP):
                files.add(p)
    return sorted(files)


# ---- the comparison ----------------------------------------------------------------------------


def check(rows: list[Row], found: list[Found]) -> list[str]:
    """One message per problem: a surface that is not listed, is preview or retired, or a runtime
    that is not listed or whose end of support is before its ``Checked`` date."""
    by_key = {r.key: r for r in rows}
    problems: list[str] = []
    for f in found:
        where = f"{f.file}:{f.line}"
        row = by_key.get(f.key)
        if row is None and f.kind == "rest" and f.method is None:
            # the call does not name its method: any listed method of that path will do
            wanted = reduce_params(f.path)
            same = [
                r
                for r in rows
                if r.kind == "rest" and reduce_params(r.surface.partition(" ")[2]) == wanted
            ]
            row = next((r for r in same if r.status != "GA"), same[0] if same else None)
        label = f"{f.kind} {f.surface}"
        if row is None:
            problems.append(f"{where}: {label} is not in the inventory (docs/FABRIC_PLATFORM.md)")
        elif f.kind == "runtime":
            if (
                row.end_of_support != NONE_ANNOUNCED
                and date.fromisoformat(row.end_of_support) < row.checked
            ):
                problems.append(
                    f"{where}: runtime {f.surface} ended support on {row.end_of_support}, "
                    f"before it was checked on {row.checked}"
                )
        elif row.status != "GA":
            problems.append(f"{where}: {label} is listed {row.status}, not GA")
    return problems


def run(root: Path = ROOT, inventory: Path | None = None) -> list[str]:
    path = inventory or root / "docs" / "FABRIC_PLATFORM.md"
    rows = parse_table(path.read_text(encoding="utf-8"))
    found: list[Found] = []
    for file in python_files(root):
        found += scan_file(file, root)
    for file in runtime_files(root):
        label = file.relative_to(root).as_posix()
        found += scan_runtimes(file.read_text(encoding="utf-8"), label)
    return check(rows, found)


def main() -> int:
    problems = run()
    for p in problems:
        print(p)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
