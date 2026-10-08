"""Install a release of ``sqllocks-shape[all]`` into a fresh environment and smoke-test it (P8-04).

The P8-04 acceptance: after the TestPyPI publish, ``sqllocks-shape[all]`` installs from TestPyPI
and passes the smoke suite on each platform. This script is that check, the same on every
runner and locally:

1. a fresh virtual environment from ``--python``;
2. ``sqllocks-shape[all]==<version>`` installed from ``--source``:
   * ``testpypi``: first-party distributions from TestPyPI, third-party ones from PyPI;
   * ``pypi``: everything from PyPI;
   * a directory: first-party distributions from that directory only (``--no-index``; pip takes
     the platform wheel when one fits, else the pure wheel), third-party ones from PyPI;
3. from a scratch directory (so no source tree can shadow the install): ``import shape`` comes
   from ``site-packages`` at ``<version>``; every first-party distribution is installed at
   ``<version>``; ``--kernel rust`` (or ``python``) is the kernel ``auto`` picks; ``shape
   plugins list`` names every first-party plugin with no load error; and ``shape suite run smoke
   --scale small`` meets every answer key.

An index can lag a fresh upload by a few minutes, so the install is retried (``--attempts``).

    python scripts/release_smoke.py --version 1.0.0 --source testpypi --kernel rust
    python scripts/release_smoke.py --version 1.0.0 --source dist --kernel any

Exit status: 0 when the install and every check pass, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_versions  # noqa: E402

TESTPYPI = "https://test.pypi.org/simple/"
PYPI = "https://pypi.org/simple/"


def first_party(root: Path = check_versions.ROOT) -> list[str]:
    """Every distribution of a release: core and each plugin."""
    names = [check_versions.CORE_NAME]
    for d in check_versions.plugin_dirs(root):
        names.append(d.name.replace("shape-", "sqllocks-shape-", 1))
    return names


def install_commands(python: Path, version: str, source: str) -> list[list[str]]:
    """The pip commands that install ``sqllocks-shape[all]==version`` from ``source``."""
    pip = [str(python), "-m", "pip", "install", "--no-cache-dir", "--disable-pip-version-check"]
    want = f"{check_versions.CORE_NAME}[all]=={version}"
    if source == "testpypi":
        return [[*pip, "--index-url", TESTPYPI, "--extra-index-url", PYPI, want]]
    if source == "pypi":
        return [[*pip, "--index-url", PYPI, want]]
    directory = str(Path(source).resolve())
    pinned = [f"{name}=={version}" for name in first_party()]
    return [
        [*pip, "--no-index", "--no-deps", "--find-links", directory, *pinned],
        [*pip, "--find-links", directory, want],
    ]


def missing_plugins(plugins_json: str) -> list[str]:
    """Problems in ``shape plugins list --json`` output: a first-party plugin missing, or an
    entry that failed to load."""
    payload = json.loads(plugins_json)["payload"]
    problems = [
        f"plugin {e.get('group')}:{e.get('name')} ({e.get('source')}): {e['error']}"
        for e in payload
        if e.get("error")
    ]
    sources = {e.get("source") for e in payload}
    problems += [
        f"no plugin entry from {name}" for name in first_party()[1:] if name not in sources
    ]
    return problems


PROBE = """
import importlib.metadata as md, json, sys
import shape
from shape.kernel import kernel_name
names = json.loads(sys.argv[1])
print(json.dumps({
    "file": shape.__file__,
    "version": shape.__version__,
    "kernel": kernel_name(),
    "installed": {n: (md.version(n) if n in {d.metadata["Name"] for d in md.distributions()}
                      else None) for n in names},
}))
"""


def probe_problems(probe: dict[str, object], version: str, kernel: str) -> list[str]:
    problems: list[str] = []
    if "site-packages" not in str(probe["file"]):
        problems.append(f"shape was imported from {probe['file']}, not the installed release")
    if probe["version"] != version:
        problems.append(f"shape.__version__ is {probe['version']!r}, expected {version!r}")
    if kernel != "any" and probe["kernel"] != kernel:
        problems.append(f"the kernel is {probe['kernel']!r}, expected {kernel!r}")
    installed = probe["installed"]
    assert isinstance(installed, dict)
    for name, got in sorted(installed.items()):
        if got != version:
            problems.append(f"{name} is installed at {got!r}, expected {version!r}")
    return problems


def _run(cmd: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, text=True, **kwargs)  # type: ignore[call-overload,no-any-return]


def smoke(python: str, version: str, source: str, kernel: str, attempts: int) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="shape-release-smoke-") as tmp:
        venv = Path(tmp) / "venv"
        _run([python, "-m", "venv", str(venv)], check=True)
        vpy = venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        for cmd in install_commands(vpy, version, source):
            for attempt in range(1, attempts + 1):
                if _run(cmd).returncode == 0:
                    break
                if attempt == attempts:
                    return [f"install failed after {attempts} attempts: {' '.join(cmd)}"]
                time.sleep(30 * attempt)  # a fresh upload can take minutes to reach the index
        work = Path(tmp) / "work"
        work.mkdir()
        env = {k: v for k, v in os.environ.items() if k not in {"SHAPE_KERNEL", "PYTHONPATH"}}
        run = _run(
            [str(vpy), "-c", PROBE, json.dumps(first_party())],
            cwd=work,
            env=env,
            capture_output=True,
        )
        if run.returncode != 0:
            return [f"import probe failed:\n{run.stdout}{run.stderr}"]
        problems = probe_problems(json.loads(run.stdout), version, kernel)
        plugins = _run(
            [str(vpy), "-m", "shape", "plugins", "list", "--json"],
            cwd=work,
            env=env,
            capture_output=True,
        )
        if plugins.returncode != 0:
            problems.append(f"shape plugins list failed:\n{plugins.stdout}{plugins.stderr}")
        else:
            problems += missing_plugins(plugins.stdout)
        suite = _run(
            [str(vpy), "-m", "shape", "suite", "run", "smoke", "--scale", "small"],
            cwd=work,
            env=env,
        )
        if suite.returncode != 0:
            problems.append(f"the smoke suite failed (exit {suite.returncode})")
        return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--version", required=True, help="the release version to install")
    p.add_argument("--source", required=True, help="testpypi, pypi or a directory of archives")
    p.add_argument("--kernel", choices=["rust", "python", "any"], default="any")
    p.add_argument("--python", default=sys.executable, help="interpreter for the environment")
    p.add_argument("--attempts", type=int, default=5, help="install attempts (index lag)")
    ns = p.parse_args(argv)
    if ns.source not in {"testpypi", "pypi"} and not Path(ns.source).is_dir():
        p.error(f"--source {ns.source!r} is not testpypi, pypi or a directory")
    problems = smoke(ns.python, ns.version, ns.source, ns.kernel, max(1, ns.attempts))
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    if not problems:
        print(f"release smoke OK: sqllocks-shape[all]=={ns.version} from {ns.source}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
