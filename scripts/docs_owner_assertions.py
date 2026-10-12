"""Verify the real records in the authenticated Avro documentation example."""

from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.request


def main() -> None:
    """Decode broker messages with the registry's authenticated schema, never a fake."""
    from confluent_kafka import Consumer
    from fastavro import schemaless_reader

    consumer = Consumer(
        {
            "bootstrap.servers": "localhost:9092",
            "group.id": "docs-avro-verification-" + os.environ["GITHUB_RUN_ID"],
            "auto.offset.reset": "earliest",
            "enable.auto.commit": False,
        }
    )
    schemas = {}
    count = 0
    consumer.subscribe(["avro-orders"])
    try:
        until = time.monotonic() + 60
        while count < 20 and time.monotonic() < until:
            message = consumer.poll(1)
            if message is None:
                continue
            assert not message.error(), message.error()
            raw = message.value()
            assert raw[0] == 0 and len(raw) > 5
            schema_id = int.from_bytes(raw[1:5], "big")
            if schema_id not in schemas:
                basic = base64.b64encode(
                    ("svc:" + os.environ["REGISTRY_PASSWORD"]).encode()
                ).decode()
                request = urllib.request.Request(
                    f"http://localhost:8081/schemas/ids/{schema_id}",
                    headers={"Authorization": "Basic " + basic},
                )
                with urllib.request.urlopen(request, timeout=10) as response:
                    schemas[schema_id] = json.loads(json.load(response)["schema"])
            record = schemaless_reader(io.BytesIO(raw[5:]), schemas[schema_id])
            assert isinstance(record, dict) and message.key() is not None
            count += 1
        assert count == 20, f"Expected 20 Avro records, read {count}"
        print("Verified 20 Confluent Avro records using the authenticated registry schema")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
