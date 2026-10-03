"""Shared vault test helpers."""

from __future__ import annotations

import base64
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


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
