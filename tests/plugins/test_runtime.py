import sys

import pytest

from shape.errors import ShapeSecurityError
from shape.plugins import PluginManifest, PluginPolicy
from shape.plugins.runtime import invoke


def test_plugin_process_roundtrip():
    r = invoke(
        [sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read())"],
        PluginManifest("x", "1"),
        PluginPolicy(),
        {"v": 2},
    )
    assert r.value == {"v": 2}


def test_plugin_failure():
    with pytest.raises(ShapeSecurityError):
        invoke(
            [sys.executable, "-c", "raise SystemExit(1)"],
            PluginManifest("x", "1"),
            PluginPolicy(),
            {},
        )
