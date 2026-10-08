"""The plugin loads, matches the plugin API major version, and is in lockstep with core."""

import shape_sqlserver

from shape.plugins import kit


def test_declares_the_supported_plugin_api():
    assert kit.check_module_api(shape_sqlserver) == "1.0"


def test_core_extra_pins_this_distribution_at_the_same_version():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    core = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    plugin = tomllib.loads(
        (root / "plugins" / "shape-sqlserver" / "pyproject.toml").read_text(encoding="utf-8")
    )["project"]
    assert core["optional-dependencies"]["sqlserver"] == [f"{plugin['name']}=={core['version']}"]
    assert plugin["version"] == core["version"]
