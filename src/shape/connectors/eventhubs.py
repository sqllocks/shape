"""Azure Event Hubs adapter; Azure SDK is optional."""

from __future__ import annotations

from ._columns import rows_to_columns


class EventHubsBatchAdapter:
    def __init__(self, decoder):
        self.decoder = decoder

    def decode_events(self, events):
        rows = [self.decoder(e.body_as_str() if hasattr(e, "body_as_str") else e) for e in events]
        return rows_to_columns(rows)

    @staticmethod
    def live_available():
        try:
            import azure.eventhub  # noqa: F401 - verify the optional SDK actually imports

            return True
        except ImportError:
            return False

    def consumer(self, connection_string, consumer_group, eventhub_name=None):
        try:
            from azure.eventhub import EventHubConsumerClient
        except ImportError as e:
            raise RuntimeError("install sqllocks-shape[eventhubs]") from e
        return EventHubConsumerClient.from_connection_string(
            connection_string, consumer_group=consumer_group, eventhub_name=eventhub_name
        )
