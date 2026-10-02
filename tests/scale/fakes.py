"""A recorded-interaction stand-in for the Fabric REST API and OneLake: a transport the Spark
router and the job tracker take in place of the network."""

from __future__ import annotations

import json
import re
from typing import Any

from shape.scale.http import FABRIC_API, ONELAKE_DFS, HttpResponse

WS = "11111111-1111-1111-1111-111111111111"
LH = "22222222-2222-2222-2222-222222222222"
NB = "33333333-3333-3333-3333-333333333333"
RUN = "44444444-4444-4444-4444-444444444444"


class FakeFabric:
    """Routes the calls the router and tracker make; records every one."""

    def __init__(self, *, has_notebook: bool = True, async_create: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.files: dict[str, bytearray] = {}
        self.notebooks: list[dict[str, str]] = (
            [{"id": NB, "displayName": "shape_spark_worker"}] if has_notebook else []
        )
        self.async_create = async_create
        self.job_status = "NotStarted"
        self.failure: dict[str, Any] | None = None
        self.queued_errors: list[int] = []
        self.runs = 0
        self.created: dict[str, Any] | None = None

    def __call__(
        self, method: str, url: str, headers: Any, body: bytes | None, timeout: float
    ) -> HttpResponse:
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout": timeout,
            }
        )
        if self.queued_errors:
            return HttpResponse(self.queued_errors.pop(0), b'{"error":"busy"}')
        path = url.split("?", 1)[0]
        if url.startswith(ONELAKE_DFS):
            return self._onelake(method, url, path, body)
        if not url.startswith(FABRIC_API):
            return HttpResponse(404, b"")
        rest = path[len(FABRIC_API) :]
        if method == "GET" and rest == f"/workspaces/{WS}/notebooks":
            return self._json({"value": self.notebooks})
        if method == "POST" and rest == f"/workspaces/{WS}/items":
            self.created = json.loads(body or b"{}")
            if self.async_create:
                return HttpResponse(202, b"", {"location": f"{FABRIC_API}/operations/op-1"})
            self.notebooks.append({"id": NB, "displayName": self.created["displayName"]})
            return self._json({"id": NB}, 201)
        if method == "GET" and rest == "/operations/op-1":
            self.notebooks.append({"id": NB, "displayName": self.created["displayName"]})
            return self._json({"status": "Succeeded"})
        if method == "POST" and rest == f"/workspaces/{WS}/items/{NB}/jobs/instances":
            self.runs += 1
            return HttpResponse(202, b"", {"location": f"{FABRIC_API}{rest}/{RUN[:-1]}{self.runs}"})
        match = re.fullmatch(
            rf"/workspaces/{WS}/items/{NB}/jobs/instances/([0-9a-f-]+)(/cancel)?", rest
        )
        if match and method == "GET":
            doc: dict[str, Any] = {"status": self.job_status}
            if self.failure:
                doc["failureReason"] = self.failure
            return self._json(doc)
        if match and method == "POST" and match.group(2):
            self.job_status = "Cancelled"
            return HttpResponse(200, b"")
        return HttpResponse(404, b'{"error":"no route"}')

    def _onelake(self, method: str, url: str, path: str, body: bytes | None) -> HttpResponse:
        if method == "PUT":
            self.files[path] = bytearray()
        elif method == "PATCH" and "action=append" in url:
            position = int(re.search(r"position=(\d+)", url).group(1))  # type: ignore[union-attr]
            assert position == len(self.files[path]), "appends must be in order"
            self.files[path] += body or b""
        elif method == "PATCH" and "action=flush" in url:
            position = int(re.search(r"position=(\d+)", url).group(1))  # type: ignore[union-attr]
            assert position == len(self.files[path]), "flush position must be the file length"
        return HttpResponse(202 if method == "PUT" else 200, b"")

    @staticmethod
    def _json(doc: Any, status: int = 200) -> HttpResponse:
        return HttpResponse(status, json.dumps(doc).encode())

    def uploaded_specs(self) -> list[dict[str, Any]]:
        return [json.loads(bytes(v)) for v in self.files.values()]

    def methods(self) -> list[tuple[str, str]]:
        return [(c["method"], c["url"].split("?", 1)[0]) for c in self.calls]
