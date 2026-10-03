"""A local GitHub-like HTTP server and webhook receiver for the W6-01 tests (loopback only)."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

TOKEN = "ghs_TESTTOKEN0123456789abcdef"


@dataclass
class Recorded:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes


@dataclass
class State:
    requests: list[Recorded] = field(default_factory=list)
    comments: list[dict[str, Any]] = field(default_factory=list)
    next_id: int = 100
    login: str | None = "shape-bot"  # None: /user answers 403, as for an installation token
    author_of_new: str = "shape-bot"
    #: (method, path prefix) -> (status, body) forced for matching requests, in order, once each
    forced: list[tuple[str, str, int, bytes, dict[str, str]]] = field(default_factory=list)
    #: webhook mode: statuses to answer with, in order (the last repeats)
    statuses: list[int] = field(default_factory=lambda: [200])


class Stub:
    def __init__(self, prefix: str = "") -> None:
        self.state = State()
        self.prefix = prefix
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:  # silence
                pass

            def _read(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def _send(
                self, status: int, payload: Any = None, headers: dict[str, str] | None = None
            ) -> None:
                data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                for k, v in (headers or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def _handle(self) -> None:
                body = self._read()
                path = self.path
                state.requests.append(
                    Recorded(self.command, path, {k: v for k, v in self.headers.items()}, body)
                )
                for i, (method, prefix, status, data, headers) in enumerate(state.forced):
                    if method == self.command and path.startswith(prefix):
                        state.forced.pop(i)
                        return self._send(status, data, headers)
                local = path[len(stub.prefix) :] if path.startswith(stub.prefix) else path
                if local == "/user":
                    if state.login is None:
                        return self._send(
                            403, {"message": "Resource not accessible by integration"}
                        )
                    return self._send(200, {"login": state.login})
                parts = local.split("?")[0].strip("/").split("/")
                if self.command == "GET" and parts[-1] == "comments":
                    query = dict(q.split("=") for q in local.partition("?")[2].split("&") if q)
                    page, per = int(query.get("page", 1)), int(query.get("per_page", 30))
                    return self._send(200, state.comments[(page - 1) * per : page * per])
                if self.command == "POST" and parts[-1] == "comments":
                    state.next_id += 1
                    item = {
                        "id": state.next_id,
                        "user": {"login": state.author_of_new},
                        "body": json.loads(body)["body"],
                    }
                    state.comments.append(item)
                    return self._send(201, item)
                if self.command == "PATCH" and parts[-2] == "comments":
                    for item in state.comments:
                        if item["id"] == int(parts[-1]):
                            item["body"] = json.loads(body)["body"]
                            return self._send(200, item)
                    return self._send(404, {"message": "Not Found"})
                return self._send(404, {"message": "Not Found"})

            do_GET = do_POST = do_PATCH = _handle

        stub = self
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{self.prefix}"

    def __enter__(self) -> Stub:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class Receiver:
    """A webhook endpoint: records requests and answers with the configured statuses."""

    def __init__(self) -> None:
        self.state = State()
        state = self.state

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def do_POST(self) -> None:
                n = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(n) if n else b""
                state.requests.append(
                    Recorded("POST", self.path, {k: v for k, v in self.headers.items()}, body)
                )
                index = min(len(state.requests) - 1, len(state.statuses) - 1)
                status = state.statuses[index]
                self.send_response(status)
                if 300 <= status < 400:
                    self.send_header("Location", "/elsewhere")
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True
        )

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}/hooks/SECRETPATH123"

    def __enter__(self) -> Receiver:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
