"""The frozen reference pools: ``pools/MANIFEST.json`` lists every shipped value list a built-in
strategy draws from, with its SHA-256 (``docs/GENERATION_STABILITY.md``, "Frozen reference pools").

Format (``format: "shape-pool-manifest"``, integer ``version``, currently 1)::

    {"format": "shape-pool-manifest", "version": 1,
     "files": {"pools/first_names.txt": {"sha256": "...", "bytes": 1234,
                                         "drawn_by": {"locale": 1, "native": 1}}}}

A key is the file's path relative to the ``shape.builtins.strategies`` package, with ``/``.
``drawn_by`` names the strategies that read the file and the generator version whose output the
file's bytes belong to. Changing a pool means raising the generator version of every strategy in
its ``drawn_by`` (keeping the old version's output) and updating the manifest in the same change:
``python -m shape.builtins.strategies.pool_manifest --write`` records the new digests and the
current versions. Unknown fields are ignored on read; a manifest written by a newer Shape (a higher
``version``) is refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FORMAT = "shape-pool-manifest"
VERSION = 1
PACKAGE_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = PACKAGE_DIR / "pools" / "MANIFEST.json"
# The folders of shipped value lists; every file in them except a manifest is a pool.
POOL_DIRS = ("pools", "locales")
_MANIFESTS = frozenset({"MANIFEST.json"})


class PoolManifestError(ValueError):
    """A pool manifest that this Shape cannot read."""


def pool_files(root: Path = PACKAGE_DIR) -> list[str]:
    """The shipped pool files under ``root``, as sorted manifest keys."""
    found = [
        f"{folder}/{p.name}"
        for folder in POOL_DIRS
        if (root / folder).is_dir()
        for p in sorted((root / folder).iterdir())
        if p.is_file() and p.name not in _MANIFESTS
    ]
    return sorted(found)


def _digest(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def parse(doc: Any, where: str = "pool manifest") -> dict[str, Any]:
    """``doc`` checked as a pool manifest (format, integer version this Shape reads, a files map
    whose entries have a ``sha256`` and a ``drawn_by`` map)."""
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise PoolManifestError(f"{where} is not a pool manifest (format is not {FORMAT!r})")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise PoolManifestError(f"{where} has no integer version")
    if version > VERSION:
        raise PoolManifestError(
            f"{where} is pool manifest version {version}, written by a newer Shape; this Shape "
            f"reads up to version {VERSION}"
        )
    files = doc.get("files")
    if not isinstance(files, dict):
        raise PoolManifestError(f"{where} has no files map")
    for name, entry in files.items():
        if not isinstance(entry, dict) or not isinstance(entry.get("sha256"), str):
            raise PoolManifestError(f"{where}: {name} has no sha256")
        drawn = entry.get("drawn_by")
        if not isinstance(drawn, dict) or not all(
            isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in drawn.values()
        ):
            raise PoolManifestError(f"{where}: {name} has no drawn_by map of generator versions")
    return doc


def load(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    """The pool manifest at ``path`` (the shipped one by default)."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PoolManifestError(f"{path} is not a readable pool manifest: {exc}") from exc
    return parse(doc, str(path))


def verify(
    manifest: Mapping[str, Any] | None = None,
    root: Path = PACKAGE_DIR,
    versions: Mapping[str, int] | None = None,
) -> list[str]:
    """What differs between the shipped pools and the manifest, one line each (empty when they
    agree): a file whose bytes changed, a file that is missing or not listed, and a strategy whose
    generator version is not the one the manifest froze the file for. ``versions`` is the current
    generator version per strategy (the installed built-ins by default)."""
    doc = manifest if manifest is not None else load()
    files: Mapping[str, Any] = doc["files"]
    current = dict(versions) if versions is not None else strategy_versions()
    problems: list[str] = []
    shipped = set(pool_files(root))
    for name in sorted(shipped - set(files)):
        problems.append(f"{name}: shipped but not listed in the pool manifest")
    for name in sorted(files):
        entry = files[name]
        path = root / name
        if not path.is_file():
            problems.append(f"{name}: listed in the pool manifest but not shipped")
            continue
        digest, size = _digest(path)
        if digest != entry["sha256"] or size != entry.get("bytes", size):
            problems.append(
                f"{name}: its bytes differ from the pool manifest (sha256 {digest}, expected "
                f"{entry['sha256']}); raise the generator version of "
                f"{', '.join(sorted(entry['drawn_by'])) or 'every strategy that reads it'} and "
                "update the manifest in the same change"
            )
        for strategy, version in sorted(entry["drawn_by"].items()):
            have = current.get(strategy)
            if have is None:
                problems.append(f"{name}: drawn_by names {strategy}, which is not a strategy")
            elif have != version:
                problems.append(
                    f"{name}: frozen for {strategy} version {version}, but {strategy} is at "
                    f"version {have}; update the pool manifest"
                )
    return problems


def strategy_versions() -> dict[str, int]:
    """The generator version of every installed strategy."""
    from shape.plugins.host import default_host

    host = default_host()
    return {
        name: int(getattr(host.get("shape.strategies", name), "generator_version", 1))
        for name in host.names("shape.strategies")
    }


def build(previous: Mapping[str, Any] | None = None, root: Path = PACKAGE_DIR) -> dict[str, Any]:
    """A manifest of the shipped pools: each file's digest and size, the ``drawn_by`` strategies
    of ``previous`` (a new file needs its ``drawn_by`` filled in by hand) at their current
    versions."""
    old: Mapping[str, Any] = (previous or {}).get("files", {})
    current = strategy_versions()
    files: dict[str, Any] = {}
    for name in pool_files(root):
        digest, size = _digest(root / name)
        drawn = old.get(name, {}).get("drawn_by", {})
        files[name] = {
            "sha256": digest,
            "bytes": size,
            "drawn_by": {s: current.get(s, v) for s, v in sorted(drawn.items())},
        }
    return {"format": FORMAT, "version": VERSION, "files": files}


def dump(doc: Mapping[str, Any]) -> str:
    return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m shape.builtins.strategies.pool_manifest",
        description="Check the reference pools against their manifest, or rewrite it.",
    )
    parser.add_argument("--write", action="store_true", help="rewrite pools/MANIFEST.json")
    args = parser.parse_args(argv)
    if args.write:
        previous = load() if MANIFEST_PATH.exists() else None
        MANIFEST_PATH.write_text(dump(build(previous)), encoding="utf-8", newline="\n")
        print(f"wrote {MANIFEST_PATH}")
        return 0
    problems = verify()
    for line in problems:
        print(line)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
