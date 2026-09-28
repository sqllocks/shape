"""Deny-by-default plugin capability contract."""

from dataclasses import dataclass

from shape.errors import ShapeSecurityError


@dataclass(frozen=True, slots=True)
class PluginCapabilities:
    network: bool = False
    filesystem_read: bool = False
    filesystem_write: bool = False
    subprocess: bool = False
    native_code: bool = False
    secrets: bool = False
    data_categories: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class PluginManifest:
    name: str
    version: str
    protocol_version: str = "1"
    capabilities: PluginCapabilities = PluginCapabilities()


@dataclass(frozen=True, slots=True)
class PluginPolicy:
    allow_network: bool = False
    allow_filesystem_write: bool = False
    allow_subprocess: bool = False
    allow_native_code: bool = False
    allow_secrets: bool = False
    allowed_categories: frozenset[str] = frozenset()

    def authorize(self, m):
        c = m.capabilities
        denied = []
        if c.network and not self.allow_network:
            denied.append("network")
        if c.filesystem_write and not self.allow_filesystem_write:
            denied.append("filesystem_write")
        if c.subprocess and not self.allow_subprocess:
            denied.append("subprocess")
        if c.native_code and not self.allow_native_code:
            denied.append("native_code")
        if c.secrets and not self.allow_secrets:
            denied.append("secrets")
        if c.data_categories and not c.data_categories.issubset(self.allowed_categories):
            denied.append("data_categories")
        if denied:
            raise ShapeSecurityError("plugin capabilities denied: " + ", ".join(denied))
