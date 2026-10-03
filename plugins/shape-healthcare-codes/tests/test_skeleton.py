"""The plugin imports and targets the plugin API major version this Shape provides."""

import shape_healthcare_codes

from shape.plugins import kit


def test_declares_the_supported_plugin_api():
    assert kit.check_module_api(shape_healthcare_codes) == "1.0"


def test_lazy_exports_resolve():
    for name in shape_healthcare_codes.__all__:
        assert getattr(shape_healthcare_codes, name) is not None
