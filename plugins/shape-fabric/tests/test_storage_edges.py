"""Storage: one filesystem per storage account (#440); local files get the usual mode (#459)."""

from __future__ import annotations

import os
import stat
import sys
from typing import Any

import pytest
from shape_fabric._storage import Storage
from shape_fabric.testing import MemoryFS

from shape.builtins.sources import azure


def test_each_storage_account_gets_its_own_filesystem(monkeypatch: pytest.MonkeyPatch) -> None:
    built: dict[str | None, MemoryFS] = {}

    def factory(loc: Any, options: Any) -> MemoryFS:
        built[loc.host] = MemoryFS()
        return built[loc.host]

    monkeypatch.setattr(azure, "_filesystem", factory)
    storage = Storage()
    storage.write_bytes("abfss://c@acctone.dfs.core.windows.net/x.txt", b"1")
    storage.write_bytes("abfss://c@accttwo.dfs.core.windows.net/y.txt", b"2")
    storage.write_bytes("abfss://d@acctone.dfs.core.windows.net/z.txt", b"3")
    assert set(built) == {"acctone.dfs.core.windows.net", "accttwo.dfs.core.windows.net"}
    assert set(built["acctone.dfs.core.windows.net"].files) == {"c/x.txt", "d/z.txt"}
    assert set(built["accttwo.dfs.core.windows.net"].files) == {"c/y.txt"}


def test_a_given_filesystem_serves_every_path() -> None:
    fs = MemoryFS()
    storage = Storage(filesystem=fs)
    storage.write_bytes("abfss://c@acctone.dfs.core.windows.net/x.txt", b"1")
    storage.write_bytes("abfss://c@accttwo.dfs.core.windows.net/y.txt", b"2")
    assert set(fs.files) == {"c/x.txt", "c/y.txt"}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_a_local_file_gets_the_mode_open_would_give(tmp_path: Any) -> None:
    old = os.umask(0o022)
    try:
        Storage().write_bytes(str(tmp_path / "out" / "a.bin"), b"x")
    finally:
        os.umask(old)
    assert stat.S_IMODE((tmp_path / "out" / "a.bin").stat().st_mode) == 0o644
