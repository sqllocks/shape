"""Shared vault test helpers."""

# ruff: noqa: I001, E402
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from _data import COLUMNS

import base64
import os

import pytest


@pytest.fixture
def kek() -> bytes:
    return bytes(range(32))


@pytest.fixture
def other_kek() -> bytes:
    return bytes(range(100, 132))


@pytest.fixture
def kek_file(tmp_path: Path, kek: bytes) -> Path:
    p = tmp_path / "kek.key"
    p.write_text(base64.b64encode(kek).decode() + "\n")
    if os.name == "posix":
        p.chmod(0o600)
    return p


@pytest.fixture
def profile_id() -> str:
    return "ab" * 32


@pytest.fixture
def profile_path(tmp_path: Path) -> Path:
    import shape

    src = tmp_path / "t.csv"
    src.write_text("id,status\n1,a\n2,b\n")
    path = tmp_path / "t.shape"
    shape.save(shape.profile(str(src)), path)
    return path


@pytest.fixture
def pair(tmp_path: Path, profile_path: Path, kek: bytes) -> tuple[Path, Path]:
    """A profile artifact and its vault (policy ``all`` columns of ``_data.COLUMNS``)."""
    from shape.artifact.io import read_artifact
    from shape.vault.format import seal_vault
    from shape.vault.ops import attach_vault, write_file

    manifest, _ = read_artifact(profile_path, notice=False)
    raw = seal_vault(COLUMNS, manifest["shape_content_id"], kek)
    vault = tmp_path / "t.shapevault"
    write_file(vault, raw)
    attach_vault(profile_path, raw)
    return profile_path, vault
