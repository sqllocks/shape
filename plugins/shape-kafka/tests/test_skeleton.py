"""The skeleton imports and targets the plugin API major version this Shape provides."""

import shape_kafka

from shape.plugins import kit


def test_declares_the_supported_plugin_api():
    assert kit.check_module_api(shape_kafka) == "1.0"
