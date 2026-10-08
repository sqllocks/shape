"""The ``eventstream://`` emitter: a Fabric Eventstream custom endpoint (P5-02).

A Fabric Eventstream with a *custom endpoint* source accepts events over the Event Hubs protocol.
Copy the endpoint's connection string (it ends in ``EntityPath=<entity>``) into
``SHAPE_EVENTSTREAM_CONNECTION_STRING`` (or pass ``connection_string``), then:

    shape emit retail --realtime --rate 500 --sink eventstream://my-eventstream

The URI's host is a label for your own use (the Eventstream's name); the entity comes from the
connection string's ``EntityPath``, or from the URI's path (``eventstream://label/entity``) when
it has none. Everything else is the Event Hubs emitter's: every message carries the idempotency
key ``<table>/<seq>`` as the property ``shape_key``, delivery is at-least-once, a throttled
service is waited for, and ``partition_key`` / ``envelope`` / ``busy_retries`` mean the same
(see :mod:`shape_eventhubs.emitter`). Microsoft Entra sign-in is not available for a custom
endpoint, so a connection string is required.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlsplit

from shape.errors import ShapeError

from .errors import MissingPluginError, missing_plugin, optional_plugin

ENV_CONNECTION_STRING = "SHAPE_EVENTSTREAM_CONNECTION_STRING"

try:
    _hubs: Any = optional_plugin("shape_eventhubs.emitter")
except MissingPluginError:  # the [eventhubs] extra: the emitter loads, and says so when used
    _hubs = None


def _require_hubs() -> Any:
    if _hubs is None:
        raise missing_plugin("shape_eventhubs")
    return _hubs


class _WithoutEventHubs:
    """The Event Hubs emitter's place when the ``[eventhubs]`` extra is not installed: the
    ``eventstream`` entry point still loads, and sending says what to install."""

    def __init__(self, client_factory: Any = None, **_options: Any) -> None:
        self._client_factory = client_factory

    def parse(self, uri: str) -> Any:
        raise missing_plugin("shape_eventhubs")

    def emit(self, uri: str, batches: Any, **options: Any) -> int:
        raise missing_plugin("shape_eventhubs")

    def close(self) -> None:
        pass


_EmitterBase: Any = _hubs.EventHubsEmitter if _hubs is not None else _WithoutEventHubs


def parse_uri(uri: str) -> Any:
    """The label and (optional) entity of an ``eventstream://`` URI (a ``HubTarget`` of the
    Event Hubs plugin)."""
    hub_target = _require_hubs().HubTarget
    parts = urlsplit(uri)
    if parts.scheme != "eventstream" or not parts.netloc:
        raise ShapeError(f"not an eventstream URI: {uri!r} (eventstream://<name>[/<entity>])")
    entity = unquote(parts.path.lstrip("/")) or None
    if entity is not None and "/" in entity:
        raise ShapeError(f"the eventstream URI takes at most one entity name: {uri!r}")
    return hub_target(parts.netloc, entity)


class EventstreamEmitter(_EmitterBase):  # type: ignore[misc]
    """Events to a Fabric Eventstream custom endpoint."""

    name = "eventstream"
    schemes = ("eventstream",)
    env_connection_string = ENV_CONNECTION_STRING

    def parse(self, uri: str) -> Any:
        return parse_uri(uri)

    def emit(self, uri: str, batches: Any, **options: Any) -> int:
        _require_hubs()
        has_connection = options.get("connection_string") or _env(self.env_connection_string)
        if not has_connection and self._client_factory is None:
            raise ShapeError(
                f"an Eventstream custom endpoint needs its connection string: pass "
                f"connection_string or set {self.env_connection_string}"
            )
        return int(super().emit(uri, batches, **options))


def _env(name: str) -> str | None:
    import os

    return os.environ.get(name)
