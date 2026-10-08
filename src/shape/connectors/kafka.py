"""
Kafka adapter. Transport dependency is optional; decoding/qualification can run without a
broker.
"""

from __future__ import annotations


class KafkaBatchAdapter:
    def __init__(self, decoder):
        self.decoder = decoder

    def decode_messages(self, messages):
        rows = [self.decoder(m.value() if hasattr(m, "value") else m) for m in messages]
        if not rows:
            return {}
        import numpy as np

        keys = rows[0].keys()
        return {k: np.asarray([r.get(k) for r in rows]) for k in keys}

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
