"""Build core and every plugin on two trees and compare published metadata bytes.

Run with ``uv run --locked python scripts/check_published_metadata.py --base origin/main
--out /tmp/shape-metadata``.
Both sdists and wheels are built with uv's normal isolated build backends. Temporary
worktrees are removed even when a build fails; --out retains archives and comparisons.
"""

from __future__ import annotations

import argparse
import difflib
import subprocess
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIELDS = (b"Name:", b"Version:", b"Requires-Dist:", b"Provides-Extra:")


def published_fields(metadata: bytes) -> bytes:
    """Keep exact header bytes (including continuation lines), without normalizing values."""
    selected = []
    keep = False
    for line in metadata.splitlines(keepends=True):
        if not line.strip():
            break
        if not line.startswith((b" ", b"\t")):
            keep = line.startswith(FIELDS)
        if keep:
            selected.append(line)
    return b"".join(selected)


def compare_metadata(before: bytes, after: bytes) -> None:
    """Reject any metadata byte change, including descriptions and unselected headers."""
    if before != after:
        detail = (
            "name, version, requirements or extras"
            if published_fields(before) != published_fields(after)
            else "other metadata bytes"
        )
        raise ValueError(f"Published metadata changed: {detail}")


def archive_metadata(path: Path) -> bytes:
    if path.suffix == ".whl":
        with zipfile.ZipFile(path) as archive:
            names = [n for n in archive.namelist() if n.endswith(".dist-info/METADATA")]
            assert len(names) == 1, path
            return archive.read(names[0])
    with tarfile.open(path) as archive:
        members = [m for m in archive.getmembers() if m.name.endswith("/PKG-INFO")]
        # Setuptools also includes an egg-info/PKG-INFO; use the top-level metadata.
        member = min(members, key=lambda m: m.name.count("/"))
        stream = archive.extractfile(member)
        assert stream is not None, path
        return stream.read()


def compare(base: str, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="shape-metadata-") as tmp:
        baseline = Path(tmp) / "base"
        subprocess.run(
            ["git", "worktree", "add", "--detach", str(baseline), base], cwd=ROOT, check=True
        )
        try:
            projects = [Path("."), *sorted(Path("plugins").glob("*/pyproject.toml"))]
            projects = [p.parent if p.name == "pyproject.toml" else p for p in projects]
            assert len(projects) == 13, projects
            differences = []
            for relative in projects:
                name = relative.name if relative != Path(".") else "core"
                metadata = {}
                for label, tree in (("base", baseline), ("lane", ROOT)):
                    dest = out / label / name
                    subprocess.run(
                        [
                            "uv",
                            "build",
                            "--no-sources",
                            "--project",
                            str(tree / relative),
                            "--out-dir",
                            str(dest),
                        ],
                        cwd=tree,
                        check=True,
                    )
                    archives = sorted(dest.glob("*.whl")) + sorted(dest.glob("*.tar.gz"))
                    assert len(archives) == 2, archives
                    metadata[label] = {}
                    for archive in archives:
                        kind = "wheel" if archive.suffix == ".whl" else "sdist"
                        raw = archive_metadata(archive)
                        (dest / f"{kind}.metadata").write_bytes(raw)
                        metadata[label][kind] = raw
                for kind in ("wheel", "sdist"):
                    before, after = metadata["base"][kind], metadata["lane"][kind]
                    try:
                        compare_metadata(before, after)
                    except ValueError:
                        differences.append(f"{name}/{kind}")
                        print(
                            "".join(
                                difflib.unified_diff(
                                    before.decode().splitlines(True),
                                    after.decode().splitlines(True),
                                    fromfile=f"base/{name}/{kind}",
                                    tofile=f"lane/{name}/{kind}",
                                )
                            )
                        )
                    else:
                        print(
                            f"IDENTICAL {name}/{kind}: name, version, requires-dist, extras",
                            flush=True,
                        )
            assert not differences, f"Published metadata changed: {differences}"
            print("Published metadata: 26 comparisons, zero differences.")
        finally:
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(baseline)], cwd=ROOT, check=True
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    compare(args.base, args.out.resolve())


if __name__ == "__main__":
    main()
