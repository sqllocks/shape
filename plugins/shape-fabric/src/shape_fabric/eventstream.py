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

from shape_eventhubs.emitter import EventHubsEmitter, HubTarget

from shape.errors import ShapeError

ENV_CONNECTION_STRING = "SHAPE_EVENTSTREAM_CONNECTION_STRING"


def parse_uri(uri: str) -> HubTarget:
    """The label and (optional) entity of an ``eventstream://`` URI."""
    parts = urlsplit(uri)
    if parts.scheme != "eventstream" or not parts.netloc:
        raise ShapeError(f"not an eventstream URI: {uri!r} (eventstream://<name>[/<entity>])")
    entity = unquote(parts.path.lstrip("/")) or None
    if entity is not None and "/" in entity:
        raise ShapeError(f"the eventstream URI takes at most one entity name: {uri!r}")
    return HubTarget(parts.netloc, entity)


class EventstreamEmitter(EventHubsEmitter):
    """Events to a Fabric Eventstream custom endpoint."""

    name = "eventstream"
    schemes = ("eventstream",)
    env_connection_string = ENV_CONNECTION_STRING

    def parse(self, uri: str) -> HubTarget:
        return parse_uri(uri)

    def emit(self, uri: str, batches: Any, **options: Any) -> int:
        has_connection = options.get("connection_string") or _env(self.env_connection_string)
        if not has_connection and self._client_factory is None:
            raise ShapeError(
                f"an Eventstream custom endpoint needs its connection string: pass "
                f"connection_string or set {self.env_connection_string}"
            )
        return super().emit(uri, batches, **options)


def _env(name: str) -> str | None:
    import os

    return os.environ.get(name)
