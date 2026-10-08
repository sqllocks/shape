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


# ---- #447: the workspace is decoded and checked; URLs carry each segment encoded once


def test_the_workspace_is_decoded_like_the_other_segments() -> None:
    assert onelake.parse("onelake://My%20Workspace/lh/Files/x").workspace == "My Workspace"


@pytest.mark.parametrize(
    "uri", ["onelake://my@ws/lh/Files/x", "onelake://ws%2Fother/lh/Files/x", "onelake://w%3As/lh"]
)
def test_a_workspace_that_would_change_the_url_is_refused(uri: str) -> None:
    with pytest.raises(ShapeError):
        onelake.parse(uri)


def test_https_percent_encodes_each_segment() -> None:
    url = onelake.parse("onelake://ws/My%20Lake/Files/a%20b%23c.csv").https()
    assert url == (
        "https://onelake.dfs.fabric.microsoft.com/ws/My%20Lake.Lakehouse/Files/a%20b%23c.csv"
    )


def test_a_percent_in_a_name_is_decoded_once() -> None:
    from shape.builtins.sources import azure

    uri = onelake.to_abfss("onelake://ws/lh/Files/100%2541.csv")
    assert azure.parse(uri).path == "lh.Lakehouse/Files/100%41.csv"


def test_plain_names_keep_their_urls() -> None:
    p = onelake.parse("onelake://ws/lh/Files/raw/a.parquet")
    assert (
        p.abfss() == "abfss://ws@onelake.dfs.fabric.microsoft.com/lh.Lakehouse/Files/raw/a.parquet"
    )
    assert (
        p.https() == "https://onelake.dfs.fabric.microsoft.com/ws/lh.Lakehouse/Files/raw/a.parquet"
    )
