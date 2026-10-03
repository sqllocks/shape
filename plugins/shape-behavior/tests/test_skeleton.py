"""The plugin targets the plugin API major version this Shape provides, and is light to import."""

import subprocess
import sys

import shape_behavior

from shape.plugins import kit


def test_declares_the_supported_plugin_api():
    assert kit.check_module_api(shape_behavior) == "1.0"


def test_importing_the_command_loads_no_heavy_modules():
    code = (
        "import sys, shape_behavior, shape_behavior.cli;"
        "bad = [m for m in ('numpy', 'pyarrow') if m in sys.modules];"
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_every_public_name_resolves():
    for name in shape_behavior.__all__:
        assert getattr(shape_behavior, name) is not None
