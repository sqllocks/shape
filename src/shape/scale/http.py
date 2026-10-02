"""A small HTTPS client for the Fabric REST API and OneLake (standard library only).

Every call goes through a *transport*, a function ``(method, url, headers, body, timeout)`` that
returns an :class:`HttpResponse`. The default transport uses ``urllib`` and refuses anything but
``https://``; tests (and a recorded-interaction harness) pass their own, so no network is needed.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE_DFS = "https://onelake.dfs.fabric.microsoft.com"


class HttpError(RuntimeError):
    """A response with an error status."""

    def __init__(self, status: int, url: str, text: str) -> None:
        super().__init__(f"HTTP {status} from {_host_path(url)}: {text[:300]}")
        self.status = status
        self.text = text


@dataclass
class HttpResponse:
    status: int
    body: bytes = b""
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else {}

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


Transport = Callable[[str, str, Mapping[str, str], "bytes | None", float], HttpResponse]


def _host_path(url: str) -> str:
    """``url`` without its query string (which can carry a SAS token)."""
    return url.split("?", 1)[0]


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> HttpResponse:
    if not url.startswith("https://"):
        raise ValueError("only https:// URLs are allowed")
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310  # nosec B310
            return HttpResponse(
                response.status,
                response.read(),
                {k.lower(): v for k, v in response.headers.items()},
            )
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read(), {k.lower(): v for k, v in exc.headers.items()})


class Http:
    """Calls with a bearer token, JSON bodies and retries of throttling and 5xx responses."""

    def __init__(
        self,
        token: str,
        transport: Transport | None = None,
        *,
        retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._transport = transport or urllib_transport
        self._retries = retries
        self._sleep = sleep

    def request(
        self,
        method: str,
        url: str,
        *,
        body: Any = None,
        raw: bytes | None = None,
        token: str | None = None,
        extra: Mapping[str, str] | None = None,
        timeout: float = 30.0,
        ok: tuple[int, ...] = (200, 201, 202, 204),
    ) -> HttpResponse:
        headers = {"Authorization": f"Bearer {token or self._token}", **(extra or {})}
        data = raw
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        for attempt in range(self._retries + 1):
            response = self._transport(method, url, headers, data, timeout)
            if response.status in ok:
                return response
            retryable = response.status in (429, 500, 502, 503, 504)
            if retryable and attempt < self._retries:
                wait = response.headers.get("retry-after", "")
                self._sleep(float(wait) if wait.isdigit() else min(2.0**attempt, 30.0))
                continue
            raise HttpError(response.status, url, response.text)
        raise AssertionError("unreachable")  # pragma: no cover
