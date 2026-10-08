import json

from shape.connectors import EventHubsBatchAdapter, KafkaBatchAdapter


def test_adapter_contracts():
    def dec(x):
        return json.loads(x.decode() if isinstance(x, bytes) else x)

    rows = [json.dumps({"id": i, "v": i % 2}).encode() for i in range(100)]
    assert KafkaBatchAdapter(dec).decode_messages(rows)["id"].tolist() == list(range(100))
    assert EventHubsBatchAdapter(dec).decode_events(rows)["id"].tolist() == list(range(100))
