"""Build the release archives that need no Rust toolchain matrix (P8-04, T-25, T-29, T-09).

The T-04 platform wheels come from the ``PyO3/maturin-action`` matrix of the release workflow.
Everything else a release publishes is built here, the same way locally and in CI:

* the T-29 pure wheel ``sqllocks_shape-<v>-py3-none-any.whl`` (``scripts/build_pure_wheel.py``);
* the core sdist ``sqllocks_shape-<v>.tar.gz`` (``maturin sdist``; it builds with Rust >= 1.85);
* every first-party plugin's wheel and sdist (``python -m build``, from a scratch copy so no
  ``build/`` or ``*.egg-info`` is left in the tree).

Before building it checks that every version agrees (``scripts/check_versions.py``) and that the
plugin skeletons hold (``scripts/check_plugin_skeletons.py``); afterwards, that ``--out`` holds
exactly the expected archives and that ``twine check --strict`` passes on all of them.

    python scripts/build_release_dist.py --out dist
    python scripts/build_release_dist.py --out dist --verify-pure      # + DM-03 wheel test
    python scripts/build_release_dist.py --out dist --no-isolation     # reuse this env's backends
    python scripts/build_release_dist.py --check-set dist   # a full release: + every T-04 wheel

Exit status: 0 when every archive was built and every check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_pure_wheel  # noqa: E402
import check_plugin_skeletons  # noqa: E402
import check_versions  # noqa: E402

ROOT = check_versions.ROOT
# T-04: one abi3 platform wheel per target (the release workflow's maturin-action matrix, in
# .github/workflows/wheels.yml), named as that matrix names it, with the platform part of the
# wheel tag each must carry.
T04_TARGETS = {
    "manylinux-x86_64": r"manylinux_2_28_x86_64",
    "manylinux-aarch64": r"manylinux_2_28_aarch64",
    "musllinux-x86_64": r"musllinux_1_2_x86_64",
    "musllinux-aarch64": r"musllinux_1_2_aarch64",
    "macos-arm64": r"macosx_\d+_\d+_arm64",
    "macos-x86_64": r"macosx_\d+_\d+_x86_64",
    "windows-x64": r"win_amd64",
}


def expected_archives(version: str, root: Path = ROOT) -> set[str]:
    """File names of every archive this script builds for ``version``."""
    names = {f"sqllocks_shape-{version}-py3-none-any.whl", f"sqllocks_shape-{version}.tar.gz"}
    for d in check_versions.plugin_dirs(root):
        stem = d.name.replace("shape-", "sqllocks_shape_", 1).replace("-", "_")
        names |= {f"{stem}-{version}-py3-none-any.whl", f"{stem}-{version}.tar.gz"}
    return names


def release_set_problems(dist: Path, version: str, root: Path = ROOT) -> list[str]:
    """Problems with a complete release directory: the archives this script builds plus exactly
    one ``cp311-abi3`` platform wheel per T-04 target, and nothing else."""
    files = {p.name for p in dist.iterdir() if p.is_file()}
    want = expected_archives(version, root)
    problems = [f"missing {name}" for name in sorted(want - files)]
    platform = sorted(files - want)
    for target, tag in T04_TARGETS.items():
        pattern = re.compile(rf"^sqllocks_shape-{re.escape(version)}-cp311-abi3-(.+\.)?{tag}\.whl$")
        hits = [name for name in platform if pattern.match(name)]
        if len(hits) != 1:
            problems.append(f"T-04 target {target}: {len(hits)} platform wheels {hits}")
        platform = [name for name in platform if name not in hits]
    problems += [f"unexpected file {name}" for name in platform]
    return problems


def _maturin() -> list[str]:
    exe = shutil.which("maturin")
    if exe:
        return [exe]
    if importlib.util.find_spec("maturin") is not None:
        return [sys.executable, "-m", "maturin"]
    raise SystemExit("maturin is not installed (it is in the [dev] extra)")


def _run(cmd: list[str], **kwargs: object) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)  # type: ignore[call-overload]


def build_plugin(plugin: Path, out: Path, *, isolated: bool) -> None:
    with tempfile.TemporaryDirectory(prefix="shape-release-plugin-") as tmp:
        src = Path(tmp) / plugin.name
        shutil.copytree(
            plugin, src, ignore=shutil.ignore_patterns("build", "*.egg-info", "__pycache__")
        )
        cmd = [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(out)]
        if not isolated:
            cmd.append("--no-isolation")
        _run([*cmd, str(src)], stdout=subprocess.DEVNULL)


def build_all(out: Path, *, isolated: bool, verify_pure: bool, python: str) -> list[str]:
    version, problems = check_versions.check(ROOT)
    problems += [f"plugin skeletons: {p}" for p in check_plugin_skeletons.check_tree()]
    if problems or version is None:
        return problems
    out.mkdir(parents=True, exist_ok=True)
    present = sorted(p.name for p in out.iterdir() if p.is_file())
    if present:
        return [f"{out} is not empty: {present[:5]}"]

    wheel = build_pure_wheel.build(out)
    problems += [f"pure wheel: {p}" for p in build_pure_wheel.check(wheel)]
    if verify_pure and not problems:
        build_pure_wheel.verify(wheel, python)
    _run([*_maturin(), "sdist", "--out", str(out)], cwd=ROOT)
    for plugin in check_versions.plugin_dirs(ROOT):
        build_plugin(plugin, out, isolated=isolated)

    built = {p.name for p in out.iterdir() if p.is_file()}
    want = expected_archives(version)
    if built != want:
        problems.append(
            f"archives: missing {sorted(want - built)}, unexpected {sorted(built - want)}"
        )
    twine = subprocess.run(
        [sys.executable, "-m", "twine", "check", "--strict", *sorted(map(str, out.iterdir()))],
        capture_output=True,
        text=True,
    )
    if twine.returncode != 0:
        problems.append(f"twine check --strict failed:\n{twine.stdout}{twine.stderr}")
    return problems


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, help="an empty or new directory")
    p.add_argument(
        "--check-set", type=Path, metavar="DIST", help="only check DIST holds a full release"
    )
    p.add_argument("--no-isolation", action="store_true", help="build plugins in this env")
    p.add_argument("--verify-pure", action="store_true", help="test the pure wheel (DM-03)")
    p.add_argument("--python", default=sys.executable, help="interpreter for --verify-pure")
    ns = p.parse_args(argv)
    if (ns.out is None) == (ns.check_set is None):
        p.error("give exactly one of --out and --check-set")
    if ns.check_set is not None:
        version, problems = check_versions.check(ROOT)
        problems += release_set_problems(ns.check_set, version or "")
        for line in problems:
            print(f"FAIL {line}", file=sys.stderr)
        if not problems:
            n = len(list(ns.check_set.iterdir()))
            print(f"release set OK: {n} archives, every T-04 target, version {version}")
        return 1 if problems else 0
    problems = build_all(
        ns.out, isolated=not ns.no_isolation, verify_pure=ns.verify_pure, python=ns.python
    )
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    if problems:
        return 1
    for path in sorted(ns.out.iterdir()):
        print(f"{path.stat().st_size:>12,}  {path.name}")
    print(f"release archives OK: {len(list(ns.out.iterdir()))} in {ns.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
