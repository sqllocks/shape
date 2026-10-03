"""OneLake and ADLS paths cannot step out of their item with '.' or '..' (#432)."""

from __future__ import annotations

import pytest
from shape_fabric import onelake
from shape_fabric._storage import Storage
from shape_fabric.lakehouse import LakehouseWriter
from shape_fabric.testing import MemoryFS, sample_batch

from shape.errors import ShapeError

ESCAPES = [
    "onelake://ws/lh/Files/../../other.Lakehouse/Files/evil.txt",
    "onelake://ws/lh/Files/%2e%2e/%2e%2e/other.Lakehouse/Files/evil.txt",
    "onelake://ws/lh/Files/./x",
    "onelake://ws/../x/Files/evil.txt",
    "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files/../../other/evil.txt",
    "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files/%2E%2E/evil.txt",
]


@pytest.mark.parametrize("uri", ESCAPES)
def test_parse_refuses_dot_segments(uri: str) -> None:
    with pytest.raises(ShapeError):
        onelake.parse(uri)


@pytest.mark.parametrize(
    "uri", [*ESCAPES, "abfss://c@acct.dfs.core.windows.net/a/../../b/evil.txt"]
)
def test_storage_writes_nothing_outside_the_item(uri: str) -> None:
    fs = MemoryFS()
    with pytest.raises(ShapeError):
        Storage(filesystem=fs).write_bytes(uri, b"x")
    assert not any(".." in path for path in fs.files)


def test_the_lakehouse_writer_refuses_a_folder_that_escapes() -> None:
    fs = MemoryFS()
    writer = LakehouseWriter("onelake://ws/lh/Files/../../victim.Lakehouse/Files", filesystem=fs)
    with pytest.raises(ShapeError):
        writer.write_table("t", [sample_batch()])
    assert not fs.files


def test_ordinary_paths_still_parse() -> None:
    p = onelake.parse("onelake://ws/lh/Files/a.b/..c/x..y")
    assert p.path == "Files/a.b/..c/x..y"
