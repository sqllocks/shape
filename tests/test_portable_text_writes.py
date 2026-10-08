"""#238: text writers emit UTF-8 and LF line ends where the platform default is CRLF."""

from __future__ import annotations

import _pyio
import builtins
import io
import os

import pytest

from shape.packs.domains import load_domain, save_domain


@pytest.fixture
def windows_like(monkeypatch):
    monkeypatch.setattr(os, "linesep", "\r\n")
    monkeypatch.setattr(builtins, "open", _pyio.open)
    monkeypatch.setattr(io, "open", _pyio.open)


def test_domain_file_has_lf_and_utf8(tmp_path, windows_like):
    from shape.packs.domains import DomainDefinition

    domain = DomainDefinition(name="café", version="1.0.0", description="Łukasz", fields=[])
    path = tmp_path / "d.json"
    save_domain(domain, path)
    raw = path.read_bytes()
    assert b"\r" not in raw
    assert load_domain(path).description == "Łukasz"


def test_static_scan_every_text_write_names_its_newline():
    """No ``write_text(..., encoding=...)`` / text-mode ``open`` in src omits ``newline``."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "src" / "shape"
    bad = []
    for p in root.rglob("*.py"):
        text = p.read_text(encoding="utf-8")
        for m in re.finditer(r"(?<!def atomic_)write_text\((?:[^()]|\([^()]*\))*\)", text):
            if "newline" not in m.group(0):
                bad.append(f"{p.relative_to(root)}: {m.group(0)[:60]}")
    assert not bad, bad
