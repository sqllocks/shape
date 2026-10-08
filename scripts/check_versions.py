"""Check that every version of a release agrees (P8-04, T-09, T-10).

One release ships the core distribution (platform wheels, the pure wheel and the sdist) and every
first-party plugin under ``plugins/``, all at one version (T-09 lockstep). That version is
written in several places, and every one must say the same thing:

* ``pyproject.toml`` ``[project] version`` (the version every core archive carries);
* ``src/shape/__init__.py`` ``__version__`` (what ``shape --version`` prints);
* ``rust/shape-kernel/Cargo.toml`` ``[package] version`` and the ``shape-kernel`` entry of its
  ``Cargo.lock``;
* every ``plugins/shape-*/pyproject.toml`` ``[project] version``;
* every first-party requirement (``sqllocks-shape``, ``sqllocks-shape-<plugin>``, with or without
  extras) in core's extras and in every plugin's dependencies and extras, each pinned with
  ``==<version>`` exactly.

    python scripts/check_versions.py                  # the versions agree
    python scripts/check_versions.py --expect 1.0.0   # ... and are 1.0.0
    python scripts/check_versions.py --tag v1.0.0     # ... and match the release tag
    python scripts/check_versions.py --release        # ... and the tree is ready to release

``--release`` adds what a published version needs: a final version (no ``.dev``, ``a``, ``b``,
``rc`` or local part; Fabric's library picker hides pre-releases, plan DM-03b), a
``## <version>`` section in ``CHANGELOG.md``, and, while the version is below 1.0, the words
"early access" in ``README.md`` (plan DM-03).

Exit status: 0 when every check holds, 1 when one fails, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORE_NAME = "sqllocks-shape"
KERNEL_CRATE = "shape-kernel"
# PEP 440 public versions in canonical form (what pyproject.toml must hold).
VERSION_RE = re.compile(
    r"(?P<release>\d+(?:\.\d+)*)(?P<pre>(?:a|b|rc)\d+)?(?P<post>\.post\d+)?(?P<dev>\.dev\d+)?"
)
# A first-party requirement: the core distribution or one of its plugins, with optional extras.
FIRST_PARTY_RE = re.compile(
    r"^\s*(?P<name>sqllocks-shape(?:-[a-z0-9]+)*)\s*(?P<extras>\[[^\]]*\])?\s*(?P<spec>[^;]*)"
)
INIT_VERSION_RE = re.compile(r'^__version__\s*=\s*"(?P<v>[^"]*)"\s*$', re.MULTILINE)


@dataclass(frozen=True)
class Location:
    """One place a release's version is written: ``where`` names it, ``value`` is what it says."""

    where: str
    value: str


def _toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


def plugin_dirs(root: Path = ROOT) -> list[Path]:
    return sorted(p for p in (root / "plugins").glob("shape-*") if (p / "pyproject.toml").is_file())


def _pins(project: dict[str, Any], where: str) -> tuple[list[Location], list[str]]:
    """First-party requirements of one ``[project]`` table: their pinned versions, and problems."""
    locations: list[Location] = []
    problems: list[str] = []
    groups = {"dependencies": project.get("dependencies", [])}
    for extra, reqs in project.get("optional-dependencies", {}).items():
        groups[f"optional-dependencies.{extra}"] = reqs
    for group, reqs in groups.items():
        for req in reqs:
            m = FIRST_PARTY_RE.match(req)
            if m is None:
                continue
            name, spec = m["name"], m["spec"].strip()
            label = f"{where} {group}: {req.strip()}"
            pin = re.fullmatch(r"==\s*(\S+)", spec)
            if pin is None:
                problems.append(
                    f"{label}: a first-party requirement must be pinned with =={{version}}"
                )
                continue
            locations.append(Location(f"{label} ({name})", pin[1]))
    return locations, problems


def collect(root: Path = ROOT) -> tuple[list[Location], list[str]]:
    """Every place the release version is written, and the problems found reading them."""
    locations: list[Location] = []
    problems: list[str] = []

    core = _toml(root / "pyproject.toml").get("project", {})
    locations.append(Location("pyproject.toml [project] version", str(core.get("version", ""))))
    pins, bad = _pins(core, "pyproject.toml")
    locations += pins
    problems += bad

    init = root / "src" / "shape" / "__init__.py"
    m = INIT_VERSION_RE.search(init.read_text(encoding="utf-8")) if init.is_file() else None
    if m is None:
        problems.append('src/shape/__init__.py: no `__version__ = "..."` line')
    else:
        locations.append(Location("src/shape/__init__.py __version__", m["v"]))

    crate = root / "rust" / KERNEL_CRATE
    if (crate / "Cargo.toml").is_file():
        cargo = _toml(crate / "Cargo.toml").get("package", {})
        locations.append(
            Location(f"rust/{KERNEL_CRATE}/Cargo.toml [package] version", str(cargo.get("version")))
        )
        lock = crate / "Cargo.lock"
        if lock.is_file():
            entries = [p for p in _toml(lock).get("package", []) if p.get("name") == KERNEL_CRATE]
            if len(entries) != 1:
                problems.append(
                    f"rust/{KERNEL_CRATE}/Cargo.lock: {len(entries)} {KERNEL_CRATE} entries"
                )
            else:
                locations.append(
                    Location(
                        f"rust/{KERNEL_CRATE}/Cargo.lock {KERNEL_CRATE}", entries[0]["version"]
                    )
                )

    for d in plugin_dirs(root):
        where = f"plugins/{d.name}/pyproject.toml"
        project = _toml(d / "pyproject.toml").get("project", {})
        locations.append(Location(f"{where} [project] version", str(project.get("version", ""))))
        pins, bad = _pins(project, where)
        locations += pins
        problems += bad
        if f"{CORE_NAME}" not in {
            m["name"] for r in project.get("dependencies", []) if (m := FIRST_PARTY_RE.match(r))
        }:
            problems.append(f"{where}: dependencies must pin {CORE_NAME}=={{version}}")
    return locations, problems


def is_final(version: str) -> bool:
    m = VERSION_RE.fullmatch(version)
    return m is not None and not (m["pre"] or m["dev"])


def check(
    root: Path = ROOT,
    *,
    expect: str | None = None,
    tag: str | None = None,
    release: bool = False,
) -> tuple[str | None, list[str]]:
    """The agreed version (``None`` when there is none) and every problem found."""
    locations, problems = collect(root)
    version = locations[0].value if locations else None
    if version is not None and VERSION_RE.fullmatch(version) is None:
        problems.append(f"pyproject.toml: {version!r} is not a canonical PEP 440 version")
    for loc in locations[1:]:
        if loc.value != version:
            problems.append(f"{loc.where}: {loc.value!r} != {version!r}")
    if expect is not None and version != expect:
        problems.append(f"version is {version!r}, expected {expect!r}")
    if tag is not None and tag != f"v{version}":
        problems.append(f"tag {tag!r} does not match the version: it must be 'v{version}'")
    if release and version is not None:
        problems += _release_problems(root, version)
    return version, problems


def _release_problems(root: Path, version: str) -> list[str]:
    problems: list[str] = []
    if not is_final(version):
        problems.append(f"{version!r} is a pre-release or dev version; a release must be final")
    changelog = root / "CHANGELOG.md"
    text = changelog.read_text(encoding="utf-8") if changelog.is_file() else ""
    heading = re.compile(rf"^## \[?{re.escape(version)}\]?(?:\s|$)", re.MULTILINE)
    if heading.search(text) is None:
        problems.append(f"CHANGELOG.md has no '## {version}' section")
    if version.startswith("0."):
        readme = root / "README.md"
        if "early access" not in readme.read_text(encoding="utf-8").lower():
            problems.append(f"README.md must say 'early access' while the version is {version}")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    p.add_argument("--expect", metavar="VERSION", help="the version must be exactly this")
    p.add_argument("--tag", metavar="TAG", help="the version must match this tag (v<version>)")
    p.add_argument("--release", action="store_true", help="also check the tree is releasable")
    p.add_argument("--list", action="store_true", help="print every location and its version")
    ns = p.parse_args(argv)
    version, problems = check(ns.root, expect=ns.expect, tag=ns.tag, release=ns.release)
    if ns.list:
        for loc in collect(ns.root)[0]:
            print(f"{loc.value:<12} {loc.where}")
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    if problems:
        return 1
    count = len(collect(ns.root)[0])
    print(
        f"versions OK: {count} locations agree on {version}" + (" (release)" if ns.release else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
