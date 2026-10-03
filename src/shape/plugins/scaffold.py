"""``shape plugins new``: a new plugin package from the templates in ``shape/plugins/template``.

There is one template for every group of ``shape.plugins.api.v1.GROUPS`` (a folder named like
the group without ``shape.``, holding the plugin module and its conformance test), and a few
files every package has. The files are rendered in memory first, so a name, a group or a folder
that cannot be used fails before anything is written.
"""

from __future__ import annotations

import argparse
import json
import keyword
import re
import sys
from importlib import resources
from pathlib import Path
from typing import Any

#: letters, digits and single hyphens between them; it becomes the distribution name, the entry
#: point name and (with hyphens as underscores) the Python package name
NAME = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
MAX_NAME = 48
_CLASS_SUFFIX = {
    "shape.sources": "Source",
    "shape.sinks": "Sink",
    "shape.detectors": "Detector",
    "shape.fitters": "Fitter",
    "shape.strategies": "Strategy",
    "shape.distributions": "Distribution",
    "shape.calendars": "Calendar",
    "shape.domains": "Domain",
    "shape.chaos": "Mutator",
    "shape.emitters": "Emitter",
    "shape.stream_sources": "StreamSource",
    "shape.transforms": "Transform",
    "shape.commands": "Command",
    "shape.reports": "Report",
}
_TOKEN = re.compile(r"\{\{(dist|package|name|class|group|protocol|authors)\}\}")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def template_dir(group: str) -> str:
    """The template folder of a group: the group's name without ``shape.``."""
    return group.removeprefix("shape.")


def _root() -> Any:
    return resources.files("shape").joinpath("plugins/template")


def has_template(group: str) -> bool:
    """Whether ``group`` has its two template files."""
    folder = _root().joinpath(template_dir(group))
    return all(folder.joinpath(n).is_file() for n in ("module.py.tmpl", "test_conformance.py.tmpl"))


def missing_templates() -> list[str]:
    """The groups of plugin API v1 that have no template (a test requires this to be empty), and
    the template folders that belong to no group, as ``folder`` names."""
    from shape.plugins.api import v1

    missing = [g for g in v1.GROUPS if not has_template(g)]
    known = {template_dir(g) for g in v1.GROUPS}
    extra = [
        f"{entry.name} (no such group)"
        for entry in _root().iterdir()
        if entry.is_dir() and entry.name not in known | {"common", "__pycache__"}
    ]
    return missing + extra


def _text(folder: str, name: str) -> str:
    text: str = _root().joinpath(folder, name).read_text(encoding="utf-8")
    return text


def check_name(name: str) -> str:
    """``name`` when it can be a package name, else ``ValueError``."""
    if not NAME.fullmatch(name) or len(name) > MAX_NAME:
        raise ValueError(
            f"invalid plugin name {name!r}: use lowercase letters, digits and single hyphens, "
            f"starting with a letter (at most {MAX_NAME} characters)"
        )
    package = name.replace("-", "_")
    if keyword.iskeyword(package) or package in ("shape", "tests", "test"):
        raise ValueError(f"invalid plugin name {name!r}: {package!r} is reserved")
    return package


def class_name(name: str, group: str) -> str:
    return "".join(part.capitalize() for part in name.split("-")) + _CLASS_SUFFIX[group]


def _authors(author: str | None) -> str:
    if author is None:
        return ""
    if not author.strip() or _CONTROL.search(author):
        raise ValueError("--author must be a single line of text")
    return f"authors = [{{name = {json.dumps(author, ensure_ascii=False)}}}]\n"


def render(name: str, group: str, author: str | None = None) -> dict[str, str]:
    """The files of the new package, by relative path. Raises ``ValueError`` for an invalid
    name, an unknown group or an author that cannot be written."""
    from shape.plugins.api import v1  # loads pyarrow: only when a package is really rendered

    if group not in v1.GROUPS:
        raise ValueError(f"unknown group {group!r}; the groups are: {', '.join(sorted(v1.GROUPS))}")
    package = check_name(name)
    if not has_template(group):
        raise ValueError(f"there is no template for the group {group!r}")
    values = {
        "dist": name,
        "package": package,
        "name": name,
        "class": class_name(name, group),
        "group": group,
        "protocol": v1.GROUPS[group],
        "authors": _authors(author),
    }

    def fill(text: str) -> str:
        return _TOKEN.sub(lambda m: values[m.group(1)], text)

    folder = template_dir(group)
    return {
        "pyproject.toml": fill(_text("common", "pyproject.toml.tmpl")),
        "README.md": fill(_text("common", "README.md.tmpl")),
        f"src/{package}/__init__.py": fill(_text(folder, "module.py.tmpl")),
        "tests/conftest.py": fill(_text("common", "conftest.py.tmpl")),
        "tests/test_conformance.py": fill(_text(folder, "test_conformance.py.tmpl")),
        ".github/workflows/ci.yml": fill(_text("common", "ci.yml.tmpl")),
    }


def target_dir(a: argparse.Namespace) -> Path:
    return Path(a.output) if a.output else Path(a.name)


def check_target(folder: Path) -> None:
    if folder.exists():
        if not folder.is_dir():
            raise ValueError(f"{folder} exists and is not a folder")
        if any(folder.iterdir()):
            raise ValueError(f"{folder} is not empty: choose another folder with -o")


def plan_files(a: argparse.Namespace) -> list[Path]:
    """The files ``new`` would create (also the dry run), after the same checks."""
    files = render(a.name, a.group, a.author)
    folder = target_dir(a)
    check_target(folder)
    return [folder / rel for rel in files]


def add_arguments(sub: Any) -> None:
    n = sub.add_parser(
        "new",
        help="create a new plugin package from a template",
        description="Create a plugin package for one group of plugin API v1: pyproject.toml with "
        "the entry point, a module that implements the group's protocol, a test that runs the "
        "conformance kit, a README and a GitHub workflow. Nothing is written when the name is "
        "invalid, the group is unknown or the folder is not empty.",
    )
    n.add_argument("name", metavar="NAME", help="package name: lowercase letters, digits, hyphens")
    n.add_argument(
        "--group", required=True, metavar="GROUP", help="entry-point group, e.g. shape.sources"
    )
    n.add_argument("-o", "--output", metavar="DIR", help="folder to create it in (default: ./NAME)")
    n.add_argument("--author", metavar="TEXT", help="author name for pyproject.toml")
    n.add_argument("--json", action="store_true", help="print the result as a shape-result")


def run(a: argparse.Namespace) -> int:
    try:
        files = render(a.name, a.group, a.author)
        folder = target_dir(a)
        check_target(folder)
    except ValueError as exc:
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2
    written = []
    for rel, text in files.items():
        path = folder / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(str(path))
    print(
        json.dumps(
            {"name": a.name, "group": a.group, "folder": str(folder), "files": written}, indent=2
        )
    )
    return 0
