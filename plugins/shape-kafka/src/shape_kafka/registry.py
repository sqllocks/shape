"""A Confluent-compatible schema registry client (REST API) and subject naming.

Only what the emitter needs: register a schema under a subject and get its id. ``register`` is
idempotent on a real registry (the same schema under the same subject gives the same id), and the
emitter registers once per table per run.

Credentials are never a literal: the user name and password come from ``--sink-config
kafka.schema_registry_username=...`` and ``kafka.schema_registry_password=env://NAME`` (a
:mod:`shape.security.credrefs` reference, resolved before they get here). A URL that carries
credentials (``https://user:pass@host``) is refused.

``transport`` is for tests: ``transport(method, url, headers, body) -> (status, body bytes)``
stands in for the network (the in-process fake registry in :mod:`shape_kafka.testing`).
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote, urlsplit

from shape.errors import ShapeError

Transport = Callable[[str, str, Mapping[str, str], bytes | None], tuple[int, bytes]]
SUBJECT_STRATEGIES = ("topic", "record", "topic_record")
CONTENT_TYPE = "application/vnd.schemaregistry.v1+json"


def subject_name(strategy: str, topic: str, record: str) -> str:
    """The registry subject: ``topic`` is ``<topic>-value``, ``record`` the record's full name,
    ``topic_record`` ``<topic>-<record full name>``."""
    if strategy == "topic":
        return f"{topic}-value"
    if strategy == "record":
        return record
    if strategy == "topic_record":
        return f"{topic}-{record}"
    raise ShapeError(
        f"unknown subject strategy {strategy!r}; choose from {', '.join(SUBJECT_STRATEGIES)}"
    )


def check_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ShapeError(f"kafka.schema_registry_url {url!r} is not an http(s) URL")
    if parts.username or parts.password:
        raise ShapeError(
            "kafka.schema_registry_url must not carry credentials: give "
            "kafka.schema_registry_username and kafka.schema_registry_password=env://NAME"
        )
    return url.rstrip("/")


def _urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None
) -> tuple[int, bytes]:
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  # nosec B310
            return int(response.status), bytes(response.read())
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ConnectionError(f"schema registry unreachable: {exc}") from exc


class RegistryClient:
    def __init__(
        self,
        url: str,
        *,
        username: str | None = None,
        password: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.url = check_url(url)
        if bool(username) != bool(password):
            raise ShapeError(
                "the schema registry needs both kafka.schema_registry_username and "
                "kafka.schema_registry_password"
            )
        self._headers = {"Content-Type": CONTENT_TYPE, "Accept": CONTENT_TYPE}
        if username and password:
            token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
            self._headers["Authorization"] = f"Basic {token}"
        self._transport = transport or _urllib_transport

    def register(self, subject: str, schema_text: str, schema_type: str) -> int:
        """Register ``schema_text`` under ``subject``; return its id. A registry that refuses
        the schema is a :class:`ShapeError` (the run stops); an unreachable or failing registry
        is a ``ConnectionError`` (the runtime retries)."""
        body: dict[str, Any] = {"schema": schema_text}
        if schema_type != "AVRO":
            body["schemaType"] = schema_type
        status, raw = self._transport(
            "POST",
            f"{self.url}/subjects/{quote(subject, safe='')}/versions",
            self._headers,
            json.dumps(body).encode("utf-8"),
        )
        if 200 <= status < 300:
            try:
                return int(json.loads(raw)["id"])
            except (ValueError, KeyError, TypeError):
                raise ShapeError(
                    f"schema registry refused the schema for subject {subject}: "
                    "the registry's answer has no schema id"
                ) from None
        if status >= 500 or status == 429:
            raise ConnectionError(f"schema registry error {status} for subject {subject}")
        raise ShapeError(
            f"schema registry refused the schema for subject {subject}: {_message(raw, status)}"
        )


def _message(raw: bytes, status: int) -> str:
    try:
        doc = json.loads(raw)
        if isinstance(doc, dict) and doc.get("message"):
            return str(doc["message"])
    except ValueError:
        pass
    text = raw.decode("utf-8", errors="replace").strip()
    return text or f"HTTP {status}"
