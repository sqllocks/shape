"""Keep adlfs's ambient credentials from overriding the resolved identity."""

from collections.abc import Mapping
from functools import cache
from typing import Any


@cache
def _configured_filesystem(base: type[Any]) -> type[Any]:
    def initialize(self: Any, **options: Any) -> None:
        self._shape_storage_keys = {
            key: options.get(key) for key in ("account_key", "sas_token", "connection_string")
        }
        base.__init__(self, **options)

    def do_connect(self: Any) -> None:
        # adlfs fills these from os.environ even when credential= was explicit. Its
        # client construction tries every credential, including an unusable shared
        # key for OneLake. Only the identity selected by Shape may reach this client.
        for key, value in self._shape_storage_keys.items():
            setattr(self, key, value)
        base.do_connect(self)

    return type("ConfiguredFileSystem", (base,), {"__init__": initialize, "do_connect": do_connect})


def filesystem(adlfs: Any, options: Mapping[str, Any]) -> Any:
    constructor = adlfs.AzureBlobFileSystem
    if isinstance(constructor, type):
        constructor = _configured_filesystem(constructor)
    return constructor(**options)
