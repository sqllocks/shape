"""
Minimal dependency-free local Shape API/dashboard; production deployments can wrap the same
service.
"""

from __future__ import annotations

import html
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from shape.query import query


class ShapeService:
    def __init__(self):
        self.shapes = {}

    def put(self, name, shape):
        self.shapes[name] = shape

    def get(self, name):
        return self.shapes[name]

    def summary(self, name):
        s = self.get(name)
        return {
            "name": name,
            "rows": s.get("rows", 0),
            "columns": len(s.get("columns", {})),
            "classification": s.get("classification", "UNSPECIFIED"),
        }

    def query(self, name, expr):
        return query(self.get(name), expr)

    def dashboard(self, name):
        x = self.summary(name)
        return (
            f"<!doctype html><meta charset=utf-8><title>Shape — {html.escape(name)}</title>"
            f"<h1>{html.escape(name)}</h1><dl><dt>Rows</dt><dd>{x['rows']}</dd>"
            f"<dt>Columns</dt><dd>{x['columns']}</dd><dt>Classification</dt>"
            f"<dd>{html.escape(str(x['classification']))}</dd></dl>"
        )


def make_handler(service, token=None):
    class H(BaseHTTPRequestHandler):
        def _json(self, obj, status=200):
            b = json.dumps(obj, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if token is not None and self.headers.get("Authorization") != "Bearer " + token:
                return self._json({"error": "unauthorized"}, 401)
            u = urlparse(self.path)
            p = u.path.strip("/").split("/")
            try:
                if u.path == "/health":
                    return self._json({"status": "ok"})
                if u.path == "/v1/shapes":
                    return self._json(
                        {"shapes": [service.summary(n) for n in sorted(service.shapes)]}
                    )
                if len(p) == 3 and p[:2] == ["v1", "shapes"]:
                    return self._json(service.summary(p[2]))
                if len(p) == 4 and p[:2] == ["v1", "shapes"] and p[3] == "query":
                    return self._json({"result": service.query(p[2], parse_qs(u.query)["q"][0])})
                if len(p) == 2 and p[0] == "dashboard":
                    b = service.dashboard(p[1]).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b)
                    return
                self._json({"error": "not_found"}, 404)
            except Exception as e:
                self._json({"error": str(e)}, 400)

        def log_message(self, *args):
            pass

    return H


def serve(service, host="127.0.0.1", port=0, token=None):
    return ThreadingHTTPServer((host, port), make_handler(service, token))


def wsgi_app(service, token=None):
    """WSGI adapter for production-capable servers without coupling Shape to one web framework."""

    def app(environ, start_response):
        try:
            if token is not None and environ.get("HTTP_AUTHORIZATION") != "Bearer " + token:
                status = "401 Unauthorized"
                obj = {"error": "unauthorized"}
            else:
                path = environ.get("PATH_INFO", "")
                parts = path.strip("/").split("/")
                if path == "/health":
                    status = "200 OK"
                    obj = {"status": "ok"}
                elif path == "/v1/shapes":
                    status = "200 OK"
                    obj = {"shapes": [service.summary(n) for n in sorted(service.shapes)]}
                elif len(parts) == 3 and parts[:2] == ["v1", "shapes"]:
                    status = "200 OK"
                    obj = service.summary(parts[2])
                else:
                    status = "404 Not Found"
                    obj = {"error": "not_found"}
        except Exception as e:
            status = "400 Bad Request"
            obj = {"error": str(e)}
        b = json.dumps(obj).encode()
        start_response(
            status, [("Content-Type", "application/json"), ("Content-Length", str(len(b)))]
        )
        return [b]

    return app
