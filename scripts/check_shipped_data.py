"""Check that reference data ships in the wheel and nothing downloads at run time.

``python scripts/check_shipped_data.py`` checks the source tree:

* every data file under ``src/shape`` is tracked (a git-ignored file is left out of the wheel);
* every data file that a module names when it loads package data exists in the tree;
* no module outside the explicit fetch modules imports a network client.

``--wheel PATH`` (repeatable) also checks that each of those data files is inside the built wheel.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CODE_SUFFIXES = {".py", ".pyi", ".pyc", ".so", ".pyd"}
SKIP_PARTS = {"__pycache__"}
DATA_NAME = re.compile(r"[\w.\- /]*\w\.(txt|json|csv|tsv|ipynb|ya?ml|parquet|xml|toml)$")
LOADS_DATA = re.compile(r"\bresources\b|\bfiles\(|\b__file__\b")
# Modules whose job is an explicit network call (a transport the caller injects and enables).
FETCH_MODULES = {"shape/scale/http.py"}
NETWORK_IMPORTS = {
    "urllib.request",
    "http.client",
    "requests",
    "httpx",
    "pooch",
    "ftplib",
    "aiohttp",
    "urllib3",
}


def _src(root: Path) -> Path:
    return root / "src" / "shape"


def data_files(root: Path = ROOT) -> list[Path]:
    return sorted(
        p
        for p in _src(root).rglob("*")
        if p.is_file() and p.suffix not in CODE_SUFFIXES and not SKIP_PARTS & set(p.parts)
    )


def _py_files(root: Path) -> list[Path]:
    return sorted(p for p in _src(root).rglob("*.py") if not SKIP_PARTS & set(p.parts))


def _rel(root: Path, path: Path) -> str:
    return path.relative_to(root / "src").as_posix()


def _is_data_root(node: ast.AST) -> bool:
    """``__file__`` or a ``files(...)`` call: where package data starts."""
    if isinstance(node, ast.Name):
        return node.id == "__file__"
    if isinstance(node, ast.Call):
        fn = node.func
        return (isinstance(fn, ast.Name) and fn.id == "files") or (
            isinstance(fn, ast.Attribute) and fn.attr == "files"
        )
    return False


def _referenced_names(tree: ast.AST) -> set[str]:
    """Data file names written inside an expression that starts from ``__file__`` or ``files()``."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Call, ast.BinOp)):
            continue
        if not any(_is_data_root(n) for n in ast.walk(node)):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                if DATA_NAME.fullmatch(sub.value):
                    names.add(sub.value.rsplit("/", 1)[-1])
    return names


def _is_network(module: str) -> bool:
    return module in NETWORK_IMPORTS or module.split(".")[0] in NETWORK_IMPORTS


def _network_imports(tree: ast.AST) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found |= {a.name for a in node.names if a.name in NETWORK_IMPORTS}
            found |= {
                a.name.split(".")[0] for a in node.names if a.name.split(".")[0] in NETWORK_IMPORTS
            }
        elif isinstance(node, ast.ImportFrom) and node.module:
            if _is_network(node.module):
                found.add(node.module)
            # `from urllib import request` imports the client module itself
            found |= {
                f"{node.module}.{a.name}"
                for a in node.names
                if f"{node.module}.{a.name}" in NETWORK_IMPORTS
            }
    return found


def _ignored(root: Path, paths: list[Path]) -> set[Path]:
    if not paths or not (root / ".git").exists():
        return set()
    out = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin", "-z"],
        cwd=root,
        input="\0".join(str(p.relative_to(root)) for p in paths) + "\0",
        capture_output=True,
        text=True,
        check=False,
    )
    return {root / n for n in out.stdout.split("\0") if n}


def check_tree(root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    files = data_files(root)
    inventory = {p.name for p in files}
    problems += [
        f"{_rel(root, p)}: reference data file is ignored by git, so the wheel omits it"
        for p in sorted(_ignored(root, files))
    ]
    for path in _py_files(root):
        text = path.read_text("utf-8")
        tree = ast.parse(text)
        rel = _rel(root, path)
        if LOADS_DATA.search(text):
            for name in sorted(_referenced_names(tree) - inventory):
                problems.append(f"{rel}: names data file {name} that is not in the source tree")
        if rel not in FETCH_MODULES:
            for mod in sorted(_network_imports(tree)):
                problems.append(
                    f"{rel}: imports {mod}, a network client, outside an explicit fetch module"
                )
    return problems


def check_wheel(root: Path, wheel: Path) -> list[str]:
    with zipfile.ZipFile(wheel) as z:
        members = set(z.namelist())
    return [
        f"{name}: reference data file is not in the wheel"
        for name in (p.relative_to(root / "src").as_posix() for p in data_files(root))
        if name not in members
    ]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wheel", action="append", type=Path, default=[])
    args = ap.parse_args(argv)
    problems = check_tree(ROOT)
    for wheel in args.wheel:
        problems += [f"{wheel.name}: {p}" for p in check_wheel(ROOT, wheel)]
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print(f"shipped data OK: {len(data_files(ROOT))} data files, no run-time download")
    return 0


if __name__ == "__main__":
    sys.exit(main())
