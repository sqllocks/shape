"""``shape lineage emit MANIFEST --to URL|file://PATH [--namespace NS] [--token-env VAR]``.

Turns a run manifest into OpenLineage ``RunEvent``s: ``START`` and then ``COMPLETE``, or ``FAIL``
when the run failed (a gate recorded as failed in the manifest). The run id of the events is a
UUID derived from the Shape run id (OpenLineage requires a UUID); the Shape run id itself, the
reproducibility tuple and the dataset id are in the run facet ``shape``. Each output table is an
output dataset with a ``schema`` facet when its output files can still be read.

``file://PATH`` appends one JSON event per line. An ``http(s)`` URL gets one POST per event; the
optional bearer token comes from the environment variable named by ``--token-env``, never from
the command line, is sent over plain ``http`` only to a loopback host, and is never printed.

Exit codes: 0 sent; 1 the destination failed (an HTTP error, no connection, a write error);
2 the input is wrong (manifest, destination, token variable, or a missing extra).

Nothing here imports OpenLineage until an event is built: :func:`plan` is plain Python.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .extras import MissingExtraError, require
from .manifest_view import ManifestError, ManifestView, load

SHAPE_API = "1.0"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INPUT = 2

DEFAULT_NAMESPACE = "shape"
PRODUCER = "https://github.com/sqllocks/shape"
FACET_SCHEMA_URL = (
    "https://github.com/sqllocks/shape/blob/main/plugins/shape-integrations/src/"
    "shape_integrations/schemas/shape-run-facet.json#/$defs/ShapeRunFacet"
)
FACET_VERSION = 1
_RUN_NAMESPACE = uuid.UUID("5f0c2f6e-2b0d-5a47-9c53-6a1d0b7d5a10")  # fixed: ids stay reproducible
_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_TIMEOUT_SECONDS = 30


class DestinationError(ValueError):
    """``--to`` is not a destination this command can write to."""


@dataclass(frozen=True)
class Dataset:
    name: str
    columns: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Plan:
    """What the events say, before any OpenLineage class is involved."""

    namespace: str
    job_name: str
    run_uuid: str
    states: tuple[tuple[str, str], ...]  # (START|COMPLETE|FAIL, ISO-8601 time) in send order
    outputs: tuple[Dataset, ...]
    facet: dict[str, Any]


def run_uuid(shape_run_id: str) -> str:
    """The OpenLineage run id (a UUID) for a Shape run id: the same input gives the same UUID."""
    return str(uuid.uuid5(_RUN_NAMESPACE, shape_run_id))


def plan(view: ManifestView, namespace: str = DEFAULT_NAMESPACE, *, now: str | None = None) -> Plan:
    """The events for ``view``. ``now`` stands in for a missing start or finish time."""
    fallback = now or datetime.now(UTC).isoformat()
    started = view.started or view.finished or fallback
    finished = view.finished or view.started or fallback
    facet: dict[str, Any] = {"version": FACET_VERSION, "runId": view.run_id}
    if view.engine_version:
        facet["engineVersion"] = view.engine_version
    if view.reproducibility:
        facet["reproducibility"] = dict(view.reproducibility)
    if view.dataset_id:
        facet["datasetId"] = view.dataset_id
    return Plan(
        namespace=namespace,
        job_name=f"shape.{view.domain}" if view.domain else "shape.run",
        run_uuid=run_uuid(view.run_id),
        states=(("START", started), ("FAIL" if view.failed else "COMPLETE", finished)),
        outputs=tuple(Dataset(t.name, t.columns) for t in view.tables),
        facet=facet,
    )


def build_events(p: Plan) -> list[dict[str, Any]]:
    """The events of ``p`` as JSON-ready dicts, built and serialized by the OpenLineage client."""
    require("openlineage.client", "openlineage", name="OpenLineage")
    from . import openlineage_events

    return openlineage_events.build(p)


# -- destinations ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Destination:
    kind: str  # "file" or "http"
    target: str  # a path, or the URL


def parse_destination(to: str) -> Destination:
    text = (to or "").strip()
    if not text:
        raise DestinationError("--to is empty: give an http(s) URL or file://PATH")
    parts = urllib.parse.urlsplit(text)
    scheme = parts.scheme.lower()
    if scheme == "file":
        path = urllib.parse.unquote(parts.path)
        if parts.netloc not in ("", "localhost"):
            raise DestinationError(f"file:// URL with a host is not supported: {_safe(text)}")
        if not path or path.endswith("/"):
            raise DestinationError(f"file:// URL has no file name: {_safe(text)}")
        return Destination("file", path)
    if scheme in ("http", "https"):
        if not parts.hostname:
            raise DestinationError(f"URL has no host: {_safe(text)}")
        return Destination("http", text)
    raise DestinationError(
        f"unsupported destination {_safe(text)!r}: use an http(s) URL or file://PATH"
    )


def _safe(url: str) -> str:
    from shape.plugins.schemes import redact

    return redact(url)


def send_file(path: str, events: list[dict[str, Any]]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8", newline="\n") as fh:
        for event in events:
            fh.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is an error: it must not carry the token to another host."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


class HttpFailure(Exception):
    """The endpoint refused an event, or could not be reached. The message holds no token."""


def send_http(url: str, events: list[dict[str, Any]], token: str | None) -> None:
    opener = urllib.request.build_opener(_NoRedirect)
    for event in events:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise HttpFailure(f"{_safe(url)} answered HTTP {exc.code} {exc.reason}") from None
        except (urllib.error.URLError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise HttpFailure(f"cannot reach {_safe(url)}: {reason}") from None


def _token(env_name: str | None, destination: Destination) -> str | None:
    if not env_name:
        return None
    token = os.environ.get(env_name, "")
    if not token:
        raise DestinationError(f"--token-env {env_name}: that environment variable is not set")
    if destination.kind == "file":
        raise DestinationError("--token-env applies to an http(s) destination, not to file://")
    parts = urllib.parse.urlsplit(destination.target)
    if parts.scheme.lower() == "http" and (parts.hostname or "").lower() not in _LOOPBACK:
        raise DestinationError(
            "refusing to send a bearer token over plain http to a non-local host: use https"
        )
    return token


# -- the command ----------------------------------------------------------------------------


def _fail(message: str, code: int) -> int:
    print(f"shape: error: {message}", file=sys.stderr)
    return code


class LineageCommand:
    name = "lineage"
    help = "Send a run manifest to OpenLineage as run events"

    def configure(self, parser: Any) -> None:
        sub = parser.add_subparsers(dest="action", required=True)
        # No abbreviations: `--token abc` must not read as `--token-env abc` and echo the secret.
        emit = sub.add_parser(
            "emit", help="turn a run manifest into OpenLineage run events", allow_abbrev=False
        )
        emit.add_argument("manifest", metavar="MANIFEST.json", help="the run manifest")
        emit.add_argument("--to", required=True, metavar="URL|file://PATH", help="destination")
        emit.add_argument(
            "--namespace", default=DEFAULT_NAMESPACE, help="job and dataset namespace"
        )
        emit.add_argument(
            "--token-env",
            metavar="VAR",
            help="name of the environment variable that holds a bearer token for an http(s) URL",
        )

    def run(self, args: Any) -> int:
        try:
            return self._emit(args)
        except MissingExtraError as exc:
            return _fail(str(exc), EXIT_INPUT)
        except (ManifestError, DestinationError) as exc:
            return _fail(str(exc), EXIT_INPUT)
        except HttpFailure as exc:
            return _fail(str(exc), EXIT_FAILED)
        except OSError as exc:
            return _fail(f"cannot write the events: {exc.strerror or exc}", EXIT_FAILED)

    def _emit(self, args: Any) -> int:
        if not str(args.namespace).strip():
            raise DestinationError("--namespace is empty")
        destination = parse_destination(args.to)
        token = _token(args.token_env, destination)
        view = load(args.manifest)
        events = build_events(plan(view, args.namespace))
        if destination.kind == "file":
            send_file(destination.target, events)
        else:
            send_http(destination.target, events, token)
        print(f"sent {len(events)} OpenLineage events for run {view.run_id}")
        return EXIT_OK
