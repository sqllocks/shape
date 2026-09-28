import pytest

from shape.errors import ShapeSecurityError
from shape.plugins import PluginCapabilities, PluginManifest, PluginPolicy


def test_default_deny():
    with pytest.raises(ShapeSecurityError):
        PluginPolicy().authorize(
            PluginManifest("x", "1", capabilities=PluginCapabilities(network=True))
        )
