"""Wait until the disposable broker can grant idempotent producer IDs.

A fresh single-node broker loads its transaction coordinator after it first answers
metadata requests. A producer that starts in that window prints a librdkafka
``GETPID ... Coordinator load in progress`` warning, which is real output the page
would then have to show. Readiness is an environment precondition, so it is awaited
here rather than hidden from, or edited into, any example's expected output.
"""

from __future__ import annotations

import sys
import time

from confluent_kafka import Producer


def _recorder(delivered: list[object]):
    return lambda err, _message: delivered.append(err)


def wait(bootstrap: str = "localhost:9092", timeout: float = 120) -> None:
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        errors: list[object] = []
        producer = Producer(
            {
                "bootstrap.servers": bootstrap,
                "enable.idempotence": True,
                "acks": "all",
                "error_cb": errors.append,
                "log_level": 0,
            }
        )
        delivered: list[object] = []
        producer.produce("docs-readiness", b"ready", on_delivery=_recorder(delivered))
        remaining = producer.flush(10)
        if remaining == 0 and delivered == [None]:
            print("Kafka: idempotent producer ID granted")
            return
        last = errors or delivered or remaining
        time.sleep(2)
    raise RuntimeError(f"Kafka never granted an idempotent producer ID: {last}")


if __name__ == "__main__":
    wait(*sys.argv[1:2])
