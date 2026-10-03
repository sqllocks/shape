"""Regression for issue 67 item 1: the Fabric lakehouse path uses forward slashes everywhere."""

from __future__ import annotations

from pathlib import PureWindowsPath

from fabric_helpers import fabric_source


def test_windows_lakehouse_path_uses_forward_slashes():
    out = fabric_source(
        'p = "/lakehouse/default/Files/x"', PureWindowsPath(r"C:\Users\ci\lakehouse")
    )
    assert out == 'p = "C:/Users/ci/lakehouse/Files/x"'
    assert "\\" not in out
