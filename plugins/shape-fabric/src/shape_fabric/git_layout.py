"""Offline preflight for a profiles folder beside Fabric Git item directories.

This checks repository separation, not the behavior of the Fabric service.
"""

from __future__ import annotations

from pathlib import Path


def check_profile_folder(repository: Path, folder: str = "shape") -> list[Path]:
    """Require profiles outside item definitions; return the checked artifact paths."""
    root = repository.resolve()
    candidate = root / folder
    if any(path.is_symlink() for path in (candidate, *candidate.parents) if path != root):
        raise ValueError("The profiles folder must not contain symlinks")
    profiles = candidate.resolve()
    if not profiles.is_relative_to(root) or profiles == root:
        raise ValueError("The profiles folder must be inside the repository")
    for parent in (profiles, *profiles.parents):
        if (parent / ".platform").exists() or (parent / "item.metadata.json").exists():
            raise ValueError(
                "The profiles folder must be beside Fabric item folders, not inside one"
            )
        if parent == root:
            break
    if not profiles.is_dir():
        raise ValueError("The profiles folder does not exist")
    for path in profiles.rglob("*"):
        if path.is_symlink():
            raise ValueError("The profiles folder must not contain symlinks")
        if path.name in (".platform", "item.metadata.json", "item.config.json"):
            raise ValueError("The profiles folder must not contain Fabric item definitions")
    artifacts = sorted(profiles.rglob("*.shape"))
    if not artifacts:
        raise ValueError("The profiles folder must contain at least one .shape file")
    return artifacts
