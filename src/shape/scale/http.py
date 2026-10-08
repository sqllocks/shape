"""A small HTTPS client for the Fabric REST API and OneLake (standard library only).

Every call goes through a *transport*, a function ``(method, url, headers, body, timeout)`` that
returns an :class:`HttpResponse`. The default transport uses ``urllib`` and refuses anything but
``https://``, and follows a redirect only on the request's own origin, so the bearer token is
never sent to another host (#275); tests (and a recorded-interaction harness) pass their own, so
no network is needed.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

FABRIC_API = "https://api.fabric.microsoft.com/v1"
ONELAKE_DFS = "https://onelake.dfs.fabric.microsoft.com"
MAX_RETRY_WAIT = 60.0  # seconds a Retry-After header may ask for at most


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
        try:
            return json.loads(self.body.decode("utf-8")) if self.body else {}
        except ValueError:  # not JSON, or not UTF-8
            from shape.security.redact import redact_text

            snippet = redact_text(self.text[:80].replace("\n", " "))
            raise ValueError(
                f"the service answered {self.status} with something that is not JSON: {snippet!r}"
            ) from None

    def json_object(self) -> dict[str, Any]:
        """The JSON body, which must be an object (an empty body is an empty object)."""
        doc = self.json()
        if not isinstance(doc, dict):
            raise ValueError(
                f"the service answered {self.status} with a JSON {type(doc).__name__}, "
                "not a JSON object"
            )
        return doc

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")


Transport = Callable[[str, str, Mapping[str, str], "bytes | None", float], HttpResponse]


def on_fabric_api(url: str) -> bool:
    """Whether ``url`` is an ``https`` URL on the Fabric API host. Follow-up URLs a response names
    (``Location``, ``continuationUri``) carry the bearer token, so only these are followed."""
    parts, api = urlsplit(url), urlsplit(FABRIC_API)
    return parts.scheme == "https" and parts.netloc.lower() == api.netloc and not parts.username


def _host_path(url: str) -> str:
    """``url`` without its query string (which can carry a SAS token)."""
    return url.split("?", 1)[0]


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """``(scheme, host, port)`` with the scheme's default port filled in; ``None`` for a URL with
    no host, with user information, or with a port that is not a number."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.username is not None or parts.password is not None or not parts.hostname:
        return None
    default = {"https": 443, "http": 80}.get(parts.scheme.lower())
    return parts.scheme.lower(), parts.hostname.lower(), port if port is not None else default


def _same_origin(a: str, b: str) -> bool:
    """Whether URLs ``a`` and ``b`` have the same scheme, host and port."""
    origin = _origin(a)
    return origin is not None and origin == _origin(b)


def same_origin(url: str, base: str) -> str:
    """``url`` when it has ``base``'s scheme, host and port; else ``ValueError``.

    A URL a service hands back (an operation ``Location``, a ``continuationUri``) is called with the
    bearer token, so it is followed only on the service's own origin."""
    if not _same_origin(url, base):
        try:
            host = urlsplit(url).hostname or url
        except ValueError:
            host = url
        raise ValueError(
            f"refusing to send the token to {host}: the service answered with a URL outside "
            f"{urlsplit(base).hostname}"
        )
    return url


def bearer_request(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None
) -> urllib.request.Request:
    """A request whose ``Authorization`` header is never copied onto a redirect: urllib's redirect
    handler copies every other header to whatever URL a ``Location`` names."""
    request = urllib.request.Request(url, data=body, method=method)  # noqa: S310
    for name, value in headers.items():
        if name.lower() == "authorization":
            request.add_unredirected_header(name, value)
        else:
            request.add_header(name, value)
    return request


def redirected_away(url: str, response: Any) -> bool:
    """True when ``response`` (after any redirects) came from another origin than ``url``."""
    return not _same_origin(str(getattr(response, "url", None) or url), url)


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Follows a redirect only to the request's own origin (scheme, host and port); any other
    3xx is the answer. urllib's default handler copies every header, ``Authorization``
    included, to whatever URL a ``Location`` names, on any scheme and host (#275)."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> Any:
        if not _same_origin(req.full_url, newurl):
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def opener() -> urllib.request.OpenerDirector:
    """A ``urllib`` opener whose redirects stay on the request's origin."""
    return urllib.request.build_opener(SameOriginRedirect())


_OPENER = opener()


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> HttpResponse:
    if not url.startswith("https://"):
        raise ValueError("only https:// URLs are allowed")
    request = bearer_request(method, url, headers, body)
    try:
        with _OPENER.open(request, timeout=timeout) as response:
            if redirected_away(url, response):
                raise HttpError(
                    response.status,
                    url,
                    "the request was redirected to another origin; its answer is not used",
                )
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
                backoff = min(2.0**attempt, 30.0)
                self._sleep(min(float(wait), MAX_RETRY_WAIT) if wait.isdigit() else backoff)
                continue
            raise HttpError(response.status, url, response.text)
        raise AssertionError("unreachable")  # pragma: no cover
