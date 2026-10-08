"""Pinned, hashed requirements for offline installs: generate them, and check them.

``python scripts/offline_lock.py generate OUTDIR`` writes one lock file per dependency set
(``core`` and every extra of ``pyproject.toml``) with ``uv pip compile --generate-hashes``. It
needs a network, so CI runs it. ``python scripts/offline_lock.py check OUTDIR`` needs none: it
fails when a lock file is missing, holds an entry that is not pinned to one version and hashed, or
does not satisfy every dependency the project declares. The lock is universal, so a dependency
whose marker holds on any supported platform or Python (a Windows-only ``tzdata``) must be in it. First-party extras (``kafka`` and the
like) are expanded to their plugin's third-party dependencies; the first-party wheels themselves
are built in the connected enclave and carried across with the wheelhouse (docs/INSTALL.md).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent
FIRST_PARTY_PREFIX = "sqllocks-shape"
PYTHON_VERSION = "3.11"
# A universal lock covers every platform and Python the project supports (T-06, docs/INSTALL.md).
LOCK_PLATFORMS = ("linux", "darwin", "win32")
LOCK_PYTHONS = ("3.11", "3.12", "3.13", "3.14")


def _needed_by_universal_lock(req: Requirement) -> bool:
    """True unless the marker is false on every supported platform and Python, not just here."""
    if req.marker is None:
        return True
    return any(
        req.marker.evaluate(
            {
                "sys_platform": platform,
                "platform_system": {"linux": "Linux", "darwin": "Darwin", "win32": "Windows"}[
                    platform
                ],
                "python_version": python,
                "python_full_version": f"{python}.0",
            }
        )
        for platform in LOCK_PLATFORMS
        for python in LOCK_PYTHONS
    )


def canonical(name: str) -> str:
    return str(canonicalize_name(name))


def parse_requirement(text: str) -> Requirement:
    return Requirement(text)


def lock_name(set_name: str) -> str:
    return f"requirements-{set_name}.txt"


def _is_first_party(req: Requirement) -> bool:
    return canonical(req.name).startswith(FIRST_PARTY_PREFIX)


def _project(root: Path, dist: str) -> dict[str, Any] | None:
    """The ``[project]`` table of core (``sqllocks-shape``) or of the plugin named ``dist``."""
    paths = [root / "pyproject.toml", *sorted((root / "plugins").glob("*/pyproject.toml"))]
    for path in paths:
        if not path.is_file():
            continue
        project: dict[str, Any] = tomllib.loads(path.read_text("utf-8"))["project"]
        if canonical(project["name"]) == canonical(dist):
            return project
    return None


def _first_party_dependencies(root: Path, req: Requirement) -> list[Requirement]:
    """What a first-party requirement brings: the distribution's own dependencies (none for
    core, whose dependencies every set already holds) and those of each extra it asks for
    (``sqllocks-shape-databases[postgres]`` carries its drivers); an extra the distribution
    does not have is an error."""
    project = _project(root, req.name)
    if project is None:
        return []
    deps = [] if canonical(req.name) == FIRST_PARTY_PREFIX else project.get("dependencies", [])
    extras = {canonical(k): v for k, v in project.get("optional-dependencies", {}).items()}
    for extra in sorted(canonical(e) for e in req.extras):
        if extra not in extras:
            raise SystemExit(f"{project['name']} has no extra {extra!r}")
        deps = [*deps, *extras[extra]]
    return [Requirement(d) for d in deps]


def _expand(root: Path, reqs: list[Requirement], seen: set[str] | None = None) -> list[Requirement]:
    """Replace first-party requirements by the third-party dependencies they bring, extras
    included (``sqllocks-shape-databases[postgres]`` brings the plugin's ``postgres`` extra)."""
    seen = set() if seen is None else seen
    out: list[Requirement] = []
    for req in reqs:
        if not _is_first_party(req):
            out.append(req)
            continue
        key = f"{canonical(req.name)}[{','.join(sorted(canonical(e) for e in req.extras))}]"
        if key in seen:
            continue
        seen.add(key)
        out += _expand(root, _first_party_dependencies(root, req), seen)
    return out


def declared_sets(root: Path = ROOT) -> dict[str, list[Requirement]]:
    """Dependency sets by name: ``core``, then each extra together with core."""
    project = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]
    core = [Requirement(d) for d in project["dependencies"]]
    sets = {"core": core}
    for extra, deps in project.get("optional-dependencies", {}).items():
        sets[extra] = core + _expand(root, [Requirement(d) for d in deps])
    return sets


_ENTRY = re.compile(
    r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*(?:\[[^\]]*\])?)\s*([^\s;\\]*)\s*(?:;([^\\]*))?"
)


def _entries(text: str) -> list[tuple[str, str, int, str]]:
    """(name, specifier, hash count, marker) per requirement line, hashes on continuations."""
    logical: list[str] = []
    current = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not current and (not line or line.startswith("#")):
            continue
        if line.startswith("#"):
            continue
        current += " " + line.rstrip("\\").strip()
        if not line.endswith("\\"):
            logical.append(current.strip())
            current = ""
    if current:
        logical.append(current.strip())
    out = []
    for item in logical:
        if item.startswith("-"):
            continue
        hashes = len(re.findall(r"--hash=sha256:[0-9a-f]{64}", item))
        body = item.split(" --hash=", 1)[0]
        m = _ENTRY.match(body)
        if m:
            out.append((m.group(1), m.group(2), hashes, (m.group(3) or "").strip()))
    return out


def check_lock(text: str, reqs: list[Requirement]) -> list[str]:
    """Problems found in one lock file against the requirements it must satisfy."""
    problems: list[str] = []
    pinned: dict[str, Version] = {}
    for name, spec, hashes, _marker in _entries(text):
        key = canonical(name.split("[", 1)[0])
        if not spec.startswith("=="):
            problems.append(f"{key}: not pinned to one version ({spec or 'no version'})")
            continue
        if hashes == 0:
            problems.append(f"{key}: no sha256 hash")
        try:
            pinned[key] = Version(spec[2:])
        except ValueError:
            problems.append(f"{key}: unreadable version {spec[2:]!r}")
    for req in reqs:
        if not _needed_by_universal_lock(req):
            continue
        key = canonical(req.name)
        if key not in pinned:
            problems.append(f"{key}: declared by pyproject.toml but missing from the lock")
        elif not req.specifier.contains(pinned[key], prereleases=True):
            problems.append(f"{key}: locked {pinned[key]} is outside declared {req.specifier}")
    return problems


def check_directory(directory: Path, root: Path = ROOT) -> list[str]:
    problems: list[str] = []
    for name, reqs in declared_sets(root).items():
        path = directory / lock_name(name)
        if not path.is_file():
            problems.append(f"{name}: lock file {path.name} is missing")
            continue
        problems += [f"{path.name}: {p}" for p in check_lock(path.read_text("utf-8"), reqs)]
    return problems


def sample_version(req: Requirement) -> str:
    """A version that satisfies ``req`` (for tests): the lowest bound, else 1.0."""
    for spec in req.specifier:
        if spec.operator in (">=", "==", "~=", ">"):
            return spec.version.rstrip(".*")
    return "1.0"


def generate(directory: Path, root: Path = ROOT) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name, reqs in declared_sets(root).items():
        source = directory / f"{name}.in"
        source.write_text("".join(f"{r}\n" for r in reqs), "utf-8")
        subprocess.run(
            [
                "uv",
                "pip",
                "compile",
                str(source),
                "--universal",
                "--generate-hashes",
                "--python-version",
                PYTHON_VERSION,
                "--output-file",
                str(directory / lock_name(name)),
                "--quiet",
            ],
            check=True,
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("command", choices=["generate", "check"])
    ap.add_argument("directory", type=Path)
    args = ap.parse_args(argv)
    if args.command == "generate":
        generate(args.directory)
    problems = check_directory(args.directory)
    for p in problems:
        print(p, file=sys.stderr)
    if problems:
        return 1
    print(f"offline lock OK: {len(declared_sets())} sets in {args.directory}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
