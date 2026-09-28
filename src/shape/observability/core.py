"""Dependency-free Shape observability primitives."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Event:
    name: str
    timestamp: float
    attributes: dict


class Metrics:
    def __init__(self):
        self._c = {}
        self._g = {}
        self._lock = threading.Lock()

    def inc(self, name, value=1, **labels):
        k = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._c[k] = self._c.get(k, 0) + value

    def gauge(self, name, value, **labels):
        with self._lock:
            self._g[(name, tuple(sorted(labels.items())))] = value

    def snapshot(self):
        return {"counters": dict(self._c), "gauges": dict(self._g)}

    def prometheus(self):
        lines = []
        for kind, data in (("counter", self._c), ("gauge", self._g)):
            for (name, labels), v in sorted(data.items()):
                ls = "{" + ",".join(f'{k}="{str(x)}"' for k, x in labels) + "}" if labels else ""
                lines.append(f"# TYPE {name} {kind}\n{name}{ls} {v}")
        return "\n".join(lines) + "\n"


class EventBus:
    def __init__(self):
        self.sinks = []

    def subscribe(self, sink):
        self.sinks.append(sink)
        return sink

    def emit(self, name, **attributes):
        e = Event(name, time.time(), attributes)
        for s in tuple(self.sinks):
            s(e)
        return e


class JSONLinesSink:
    def __init__(self, path):
        self.path = path

    def __call__(self, e):
        with open(self.path, "a") as f:
            f.write(
                json.dumps(
                    {"name": e.name, "timestamp": e.timestamp, "attributes": e.attributes},
                    sort_keys=True,
                )
                + "\n"
            )


class WebhookSink:
    def __init__(self, url, headers=None, timeout=10, formatter=None):
        self.url = url
        self.headers = headers or {}
        self.timeout = timeout
        self.formatter = formatter or (
            lambda e: {"event": e.name, "timestamp": e.timestamp, "attributes": e.attributes}
        )

    def __call__(self, e):
        import urllib.request

        b = json.dumps(self.formatter(e)).encode()
        req = urllib.request.Request(
            self.url,
            data=b,
            headers={"Content-Type": "application/json", **self.headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return r.status


def slack_formatter(e):
    return {
        "text": f"Shape alert: {e.name}",
        "attachments": [{"text": json.dumps(e.attributes, sort_keys=True)}],
    }


def teams_formatter(e):
    return {"type": "message", "text": f"Shape alert: {e.name}", "facts": e.attributes}


def pagerduty_formatter(e):
    return {
        "routing_key": e.attributes.get("routing_key", ""),
        "event_action": "trigger",
        "payload": {
            "summary": f"Shape alert: {e.name}",
            "source": "shape",
            "severity": e.attributes.get("severity", "warning"),
            "custom_details": e.attributes,
        },
    }


class AlertRouter:
    def __init__(self):
        self.routes = []

    def add(self, predicate, sink):
        self.routes.append((predicate, sink))
        return self

    def __call__(self, e):
        return [sink(e) for pred, sink in self.routes if pred(e)]
