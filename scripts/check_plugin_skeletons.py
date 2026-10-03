"""Check the first-party plugin distributions under ``plugins/`` (P2-06, T-09).

Without arguments: every T-09 distribution exists, its name, version and core pin follow the
lockstep rule (same version as core, ``sqllocks-shape==<that version>``), it ships core's
LICENSE and a package that declares ``SHAPE_API``, and any ``shape.*`` entry point it
declares names a real group.

``--build OUT``: also build each one into a wheel in ``OUT`` (``--no-isolation`` reuses the
current environment's setuptools instead of downloading it) and check the wheel: pure Python
(``py3-none-any``), the right name and version, the package and licence inside.

Exit 0 when everything holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
# T-09 / section 5 layout: the first-party distributions.
EXPECTED = (
    "kafka",
    "eventhubs",
    "fabric",
    "sqlserver",
    "domains",
    "simulation",
    "dbt",
    "behavior",
    "healthcare-codes",
)


def core_version() -> str:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return str(data["project"]["version"])


def check_tree() -> list[str]:
    from shape.plugins.api.v1 import GROUPS

    problems: list[str] = []
    version = core_version()
    licence = (ROOT / "LICENSE").read_text(encoding="utf-8")
    present = sorted(p.name for p in PLUGINS.glob("shape-*") if p.is_dir())
    for short in EXPECTED:
        if f"shape-{short}" not in present:
            problems.append(f"plugins/shape-{short}: missing")
    for extra in sorted(set(present) - {f"shape-{s}" for s in EXPECTED}):
        problems.append(f"plugins/{extra}: not a T-09 distribution")
    for short in EXPECTED:
        d = PLUGINS / f"shape-{short}"
        if not d.is_dir():
            continue
        where = f"plugins/shape-{short}"
        try:
            meta = tomllib.loads((d / "pyproject.toml").read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            problems.append(f"{where}/pyproject.toml: {exc}")
            continue
        proj = meta.get("project", {})
        if proj.get("name") != f"sqllocks-shape-{short}":
            problems.append(f"{where}: name is {proj.get('name')!r}, want sqllocks-shape-{short}")
        if proj.get("version") != version:
            problems.append(f"{where}: version {proj.get('version')!r} != core {version!r}")
        if f"sqllocks-shape=={version}" not in proj.get("dependencies", []):
            problems.append(f"{where}: dependencies must contain 'sqllocks-shape=={version}'")
        for group in proj.get("entry-points", {}):
            if group not in GROUPS:
                problems.append(f"{where}: entry-point group {group!r} is not a plugin API group")
        if not (d / "LICENSE").is_file() or (d / "LICENSE").read_text(encoding="utf-8") != licence:
            problems.append(f"{where}: LICENSE must equal the repository LICENSE")
        init = d / "src" / f"shape_{short.replace('-', '_')}" / "__init__.py"
        if not init.is_file() or "SHAPE_API" not in init.read_text(encoding="utf-8"):
            problems.append(f"{where}: {init.relative_to(d)} must declare SHAPE_API")
    return problems


def build_wheels(out: Path, *, isolated: bool) -> list[str]:
    problems: list[str] = []
    out.mkdir(parents=True, exist_ok=True)
    version = core_version()
    for short in EXPECTED:
        d = PLUGINS / f"shape-{short}"
        # Build from a scratch copy so no build/ or *.egg-info is left in the source tree
        # (a stale build/lib would leak into later wheels).
        tmp = tempfile.mkdtemp(prefix="shape-skeleton-")
        try:
            src = Path(tmp) / d.name
            shutil.copytree(
                d, src, ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__")
            )
            cmd = [sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", str(out)]
            if not isolated:
                cmd.append("--no-build-isolation")
            run = subprocess.run([*cmd, str(src)], capture_output=True, text=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        if run.returncode != 0:
            problems.append(f"plugins/shape-{short}: build failed\n{run.stdout}{run.stderr}")
            continue
        wheels = sorted(out.glob(f"sqllocks_shape_{short.replace('-', '_')}-{version}-*.whl"))
        if len(wheels) != 1:
            problems.append(f"plugins/shape-{short}: expected one wheel, found {wheels}")
            continue
        wheel = wheels[0]
        if not wheel.name.endswith("-py3-none-any.whl"):
            problems.append(f"{wheel.name}: must be a pure py3-none-any wheel")
        with zipfile.ZipFile(wheel) as z:
            names = set(z.namelist())
        pkg = f"shape_{short.replace('-', '_')}/__init__.py"
        if pkg not in names:
            problems.append(f"{wheel.name}: missing {pkg}")
        if not any(n.endswith(".dist-info/licenses/LICENSE") for n in names):
            problems.append(f"{wheel.name}: missing LICENSE")
        if any(n.startswith("tests/") for n in names):
            problems.append(f"{wheel.name}: ships its tests")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--build", type=Path, metavar="OUT", help="also build wheels into OUT")
    p.add_argument("--no-isolation", action="store_true", help="build with the current setuptools")
    ns = p.parse_args(argv)
    problems = check_tree()
    if ns.build is not None:
        problems += build_wheels(ns.build, isolated=not ns.no_isolation)
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    if not problems:
        built = f"; {len(EXPECTED)} wheels built" if ns.build is not None else ""
        print(
            f"plugin skeletons OK ({len(EXPECTED)} distributions, version {core_version()}){built}"
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
