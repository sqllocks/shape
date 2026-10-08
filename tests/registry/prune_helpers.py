"""W8-03: helpers for the registry pruning tests: commits at chosen times, and a byte-for-byte
snapshot of a registry directory."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import shape.registry.local as local_mod
from shape.registry import LocalRegistry

#: the first commit time of every scenario: 2026-06-01T00:00:00Z
T0 = datetime(2026, 6, 1, tzinfo=UTC)


def day(n: int) -> datetime:
    """``T0`` plus ``n`` days."""
    return T0 + timedelta(days=n)


class Clock:
    """Sets the time a commit records (``created_at``)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = T0.timestamp()
        monkeypatch.setattr(local_mod, "_now", lambda: self.now)

    def at(self, moment: datetime) -> Clock:
        self.now = moment.timestamp()
        return self


def commit_at(
    clock: Clock,
    reg: LocalRegistry,
    name: str,
    data: bytes,
    moment: datetime,
    **kw: Any,
) -> str:
    clock.at(moment)
    return reg.commit(name, data, **kw)


def snapshot(root: Path) -> dict[str, bytes | None]:
    """Every path under ``root`` with its bytes (``None`` for a directory)."""
    out: dict[str, bytes | None] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        out[rel] = None if p.is_dir() else p.read_bytes()
    return out


def object_ids(root: Path) -> set[str]:
    return {p.name for p in (root / "objects").iterdir() if p.is_file()}


def assert_nothing_dangles(reg: LocalRegistry) -> None:
    """Every log entry, ref and tag of every name resolves to an intact object."""
    for name in reg.names():
        for e in reg.log(name):
            assert reg.checkout(name, e["content_id"])
        for ref, cid in reg.refs(name).items():
            if ref.startswith(".tmp-"):
                continue
            assert reg.checkout(name, ref)
            assert reg.entry(name, ref)["content_id"] == cid
        for tag, cid in reg.tags(name).items():
            assert reg.checkout(name, tag)
            assert reg.entry(name, tag)["content_id"] == cid
