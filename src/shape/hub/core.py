"""Remote Shape Hub protocol. Network-independent core plus stdlib HTTP transport."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass


class HubError(RuntimeError):
    pass


class HTTPTransport:
    def __init__(self, base_url, token=None, timeout=30):
        self.base = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode()
        h = {"Content-Type": "application/json"}
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, data=data, headers=h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except Exception as e:
            raise HubError(str(e)) from e


@dataclass(frozen=True, slots=True)
class ShapeRef:
    organization: str
    project: str
    name: str
    ref: str = "latest"


class ShapeHub:
    def __init__(self, transport):
        self.t = transport

    def push(self, ref, shape, metadata=None):
        return self.t.request(
            "POST",
            f"/v1/{ref.organization}/{ref.project}/{ref.name}/versions",
            {"shape": shape, "metadata": metadata or {}},
        )

    def pull(self, ref):
        return self.t.request(
            "GET", f"/v1/{ref.organization}/{ref.project}/{ref.name}/refs/{ref.ref}"
        )["shape"]

    def promote(self, ref, target):
        return self.t.request(
            "POST",
            f"/v1/{ref.organization}/{ref.project}/{ref.name}/promote",
            {"source": ref.ref, "target": target},
        )

    def versions(self, ref):
        return self.t.request("GET", f"/v1/{ref.organization}/{ref.project}/{ref.name}/versions")


class InMemoryHubBackend:
    def __init__(self):
        self.objects = {}
        self.refs = {}
        self.logs = {}

    def push(self, o, p, n, shape, metadata=None):
        import hashlib
        import time

        raw = json.dumps(shape, sort_keys=True, separators=(",", ":")).encode()
        h = hashlib.sha256(raw).hexdigest()
        self.objects[h] = shape
        key = (o, p, n)
        self.refs[(o, p, n, "latest")] = h
        self.logs.setdefault(key, []).append(
            {"content_id": h, "metadata": metadata or {}, "created_at": time.time()}
        )
        return {"content_id": h}

    def pull(self, o, p, n, ref):
        h = self.refs.get((o, p, n, ref), ref)
        if h not in self.objects:
            raise KeyError(f"{o}/{p}/{n}@{ref}")
        return {"content_id": h, "shape": self.objects[h]}

    def promote(self, o, p, n, source, target):
        h = self.refs.get((o, p, n, source), source)
        if h not in self.objects:
            raise KeyError(source)
        self.refs[(o, p, n, target)] = h
        return {"content_id": h, "ref": target}

    def versions(self, o, p, n):
        return self.logs.get((o, p, n), [])


def make_hub_handler(backend, token=None):
    from http.server import BaseHTTPRequestHandler

    class H(BaseHTTPRequestHandler):
        def _send(self, obj, status=200):
            b = json.dumps(obj, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def _auth(self):
            return token is None or self.headers.get("Authorization") == "Bearer " + token

        def do_GET(self):
            if not self._auth():
                return self._send({"error": "unauthorized"}, 401)
            parts = self.path.split("?")[0].strip("/").split("/")
            try:
                if len(parts) == 5 and parts[0] == "v1" and parts[4] == "versions":
                    return self._send(backend.versions(*parts[1:4]))
                if len(parts) == 6 and parts[0] == "v1" and parts[4] == "refs":
                    return self._send(backend.pull(*parts[1:4], parts[5]))
                self._send({"error": "not_found"}, 404)
            except Exception as e:
                self._send({"error": str(e)}, 404)

        def do_POST(self):
            if not self._auth():
                return self._send({"error": "unauthorized"}, 401)
            parts = self.path.strip("/").split("/")
            body = json.loads(
                self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}"
            )
            try:
                if len(parts) == 5 and parts[0] == "v1" and parts[4] == "versions":
                    return self._send(
                        backend.push(*parts[1:4], body["shape"], body.get("metadata"))
                    )
                if len(parts) == 5 and parts[0] == "v1" and parts[4] == "promote":
                    return self._send(backend.promote(*parts[1:4], body["source"], body["target"]))
                self._send({"error": "not_found"}, 404)
            except Exception as e:
                self._send({"error": str(e)}, 400)

        def log_message(self, *args):
            pass

    return H


def serve_hub(backend=None, host="127.0.0.1", port=0, token=None):
    from http.server import ThreadingHTTPServer

    return ThreadingHTTPServer(
        (host, port), make_hub_handler(backend or InMemoryHubBackend(), token)
    )
