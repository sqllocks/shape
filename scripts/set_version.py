"""Set the release version everywhere it is written (P8-04, T-09).

    python scripts/set_version.py 1.0.0            # rewrite every location, then check them
    python scripts/set_version.py 1.0.0 --dry-run  # print the files it would change

It rewrites exactly the locations ``scripts/check_versions.py`` checks (core and plugin
``pyproject.toml`` versions and first-party ``==`` pins, ``__version__``, the kernel crate's
``Cargo.toml`` and ``Cargo.lock``) and nothing else: version strings in documents, test vectors
and file formats (``first_release`` markers, ``min_shape_version`` fields) are history, not the
release version. It refuses to start from a tree whose versions disagree, and writes no file
unless every rewrite succeeds and ``check_versions.py --expect <new>`` then passes.

Changing the version is an owner action (plan §2.3, 2026-10-03): run it only for a release the
owner has approved, as the separate commit the release checklist describes.

Exit status: 0 on success, 1 when a check fails, 2 on a usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_versions as cv  # noqa: E402


def _sub_once(pattern: str, repl: str, text: str, where: str) -> str:
    new, n = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE)
    if n != 1:
        raise ValueError(f"{where}: pattern {pattern!r} not found")
    return new


def _pins(text: str, old: str, new: str) -> str:
    pin = re.compile(
        r"""(?P<head>["']sqllocks-shape(?:-[a-z0-9]+)*(?:\[[^\]]*\])?\s*==\s*)"""
        + re.escape(old)
        + r"""(?P<tail>["';\s])"""
    )
    return pin.sub(lambda m: f"{m['head']}{new}{m['tail']}", text)


def _project_version(text: str, old: str, new: str, where: str) -> str:
    return _sub_once(rf'^(version\s*=\s*"){re.escape(old)}(")', rf"\g<1>{new}\g<2>", text, where)


def _pyproject(text: str, *, old: str, new: str, where: str) -> str:
    return _pins(_project_version(text, old, new, where), old, new)


def plan(root: Path, new: str) -> dict[Path, str]:
    """The new text of every file whose version changes (nothing is written)."""
    old, problems = cv.check(root)
    if problems or old is None:
        raise ValueError("the versions disagree before the change:\n" + "\n".join(problems))
    if cv.VERSION_RE.fullmatch(new) is None:
        raise ValueError(f"{new!r} is not a canonical PEP 440 version")
    files: dict[Path, str] = {}

    def edit(path: Path, change: Callable[[str], str]) -> None:
        files[path] = change(files.get(path, path.read_text(encoding="utf-8")))

    for pyproject in [
        root / "pyproject.toml",
        *(d / "pyproject.toml" for d in cv.plugin_dirs(root)),
    ]:
        edit(
            pyproject, partial(_pyproject, old=old, new=new, where=str(pyproject.relative_to(root)))
        )
    init = root / "src" / "shape" / "__init__.py"
    edit(
        init,
        lambda t: _sub_once(
            rf'^(__version__\s*=\s*"){re.escape(old)}(")', rf"\g<1>{new}\g<2>", t, "__init__.py"
        ),
    )
    crate = root / "rust" / cv.KERNEL_CRATE
    if (crate / "Cargo.toml").is_file():
        edit(crate / "Cargo.toml", lambda t: _project_version(t, old, new, "Cargo.toml"))
    if (crate / "Cargo.lock").is_file():
        edit(
            crate / "Cargo.lock",
            lambda t: _sub_once(
                rf'^(name = "{cv.KERNEL_CRATE}"\nversion = "){re.escape(old)}(")',
                rf"\g<1>{new}\g<2>",
                t,
                "Cargo.lock",
            ),
        )
    return {p: t for p, t in files.items() if t != p.read_text(encoding="utf-8")}


def set_version(root: Path, new: str, *, dry_run: bool = False) -> list[Path]:
    """Rewrite every location to ``new``; return the changed files. Raises on any failure."""
    changes = plan(root, new)
    if dry_run:
        return sorted(changes)
    originals = {p: p.read_text(encoding="utf-8") for p in changes}
    for path, text in changes.items():
        path.write_text(text, encoding="utf-8")
    _, problems = cv.check(root, expect=new)
    if problems:
        for path, text in originals.items():  # leave the tree as it was
            path.write_text(text, encoding="utf-8")
        raise ValueError("after the rewrite the versions still disagree:\n" + "\n".join(problems))
    return sorted(changes)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("version", help="the new version, for example 1.0.0")
    p.add_argument("--root", type=Path, default=cv.ROOT, help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true", help="print the files, change nothing")
    ns = p.parse_args(argv)
    try:
        changed = set_version(ns.root, ns.version, dry_run=ns.dry_run)
    except ValueError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    for path in changed:
        print(path.relative_to(ns.root))
    verb = "would change" if ns.dry_run else "changed"
    print(f"{verb} {len(changed)} files to version {ns.version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
