"""
Kafka adapter. Transport dependency is optional; decoding/qualification can run without a
broker.
"""

from __future__ import annotations

from ._columns import rows_to_columns


class KafkaBatchAdapter:
    """Decode Kafka message values into column batches."""

    def __init__(self, decoder):
        self.decoder = decoder

    def decode_messages(self, messages):
        rows = [self.decoder(m.value() if hasattr(m, "value") else m) for m in messages]
        return rows_to_columns(rows)

    @staticmethod
    def live_available():
        try:
            import confluent_kafka  # noqa: F401 - verify the optional SDK actually imports

            return True
        except ImportError:
            return False

    def consumer(self, config):
        try:
            from confluent_kafka import Consumer
        except ImportError as e:
            raise RuntimeError("install sqllocks-shape[kafka]") from e
        return Consumer(config)
