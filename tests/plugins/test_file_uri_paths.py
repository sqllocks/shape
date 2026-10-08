"""#241: ``file:///C:/...`` and ``file://server/share/...`` keep their drive and host on Windows,
and the sink helper, the source helper and the streaming source agree."""

from __future__ import annotations

from pathlib import Path, PureWindowsPath

import pytest

from shape.builtins.sources.files import local_path as source_local_path
from shape.plugins import schemes
from shape.plugins.schemes import local_path as sink_local_path
from shape.streaming.file_source import _path_of as stream_path_of


def win(uri: str) -> PureWindowsPath:
    return PureWindowsPath(schemes.file_uri_path(uri, windows=True))


@pytest.mark.parametrize(
    ("uri", "expected"),
    [
        ("file:///C:/data/x.csv", "C:\\data\\x.csv"),
        ("file:///c:/data/x.csv", "c:\\data\\x.csv"),
        ("file://localhost/C:/data/x.csv", "C:\\data\\x.csv"),
        ("FILE:///C:/data/my%20file.csv", "C:\\data\\my file.csv"),
        ("file:///C:/", "C:\\"),
        ("file://server/share/x.csv", "\\\\server\\share\\x.csv"),
        ("file://server/share/a%20b/x.csv", "\\\\server\\share\\a b\\x.csv"),
        ("file:///data/x.csv", "\\data\\x.csv"),  # drive-less stays drive-less
        # a drive path written straight after file:// (f"file://{path}" on Windows)
        ("file://C:/data/x.csv", "C:\\data\\x.csv"),
        ("file://C:\\data\\x.csv", "C:\\data\\x.csv"),
        ("file://c|/data/x.csv", "c:\\data\\x.csv"),
    ],
)
def test_windows_uris_keep_drive_and_host(uri, expected):
    assert str(win(uri)) == expected


def test_windows_drive_is_kept():
    assert win("file:///C:/data/x.csv").drive == "C:"
    assert win("file://server/share/x.csv").drive == "\\\\server\\share"


def test_posix_reading_is_unchanged():
    assert schemes.file_uri_path("file:///tmp/a%20b.csv", windows=False) == "/tmp/a b.csv"
    assert schemes.file_uri_path("file://localhost/tmp/x", windows=False) == "/tmp/x"


def test_plain_paths_and_drive_paths_pass_through():
    assert sink_local_path("out/x.csv") == Path("out/x.csv")
    assert sink_local_path(Path("out")) == Path("out")
    assert source_local_path("out/x.csv") == Path("out/x.csv")
    assert win("file:///C:/a/b").parts[:2] == ("C:\\", "a")


def test_the_three_readers_agree_on_posix_uris(tmp_path):
    uri = (tmp_path / "a b.csv").as_uri()
    assert sink_local_path(uri) == source_local_path(uri) == tmp_path / "a b.csv"
    assert Path(stream_path_of(uri)) == tmp_path / "a b.csv"


def test_the_helpers_delegate_to_the_shared_reader(monkeypatch):
    seen = []

    def fake(uri, *, windows=None):
        seen.append(uri)
        return "/x"

    monkeypatch.setattr(schemes, "file_uri_path", fake, raising=False)
    sink_local_path("file:///C:/data/x.csv")
    source_local_path("file:///C:/data/x.csv")
    assert seen == ["file:///C:/data/x.csv"] * 2
