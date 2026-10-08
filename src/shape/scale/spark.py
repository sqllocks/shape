"""``FabricSparkRouter``: submit a generation run to a Fabric Spark notebook (P6-13).

Submitting is three steps, each one REST call or a few:

1. *prepare*: the job spec (the generation schema, seed, row counts, chunk size, sinks) is written
   to OneLake ``Files/shape_jobs/<run id>.json``, and the worker notebook is found in the
   workspace or created from the bundled template;
2. *submit*: one Jobs API call starts the notebook, passing the spec path in the Spark conf;
3. the notebook (:mod:`shape.scale.spark_worker`) reads the spec, generates, writes Delta tables
   to the Lakehouse and records ``<run id>_result.json``.

Status and cancel go through :class:`~shape.scale.jobs.FabricJobTracker`. Nothing here talks to
the network except through the injected ``transport`` (default: HTTPS with ``urllib``), and no
token is stored.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from shape.scale.http import FABRIC_API, ONELAKE_DFS, Http, Transport, on_fabric_api, same_origin
from shape.scale.jobs import JobRecord

logger = logging.getLogger(__name__)

DEFAULT_NOTEBOOK = "shape_spark_worker"
SPEC_DIR = "shape_jobs"
# Matched with fullmatch: ``$`` with ``match`` would let a trailing newline through.
_GUID = re.compile(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_ .-]{0,127}")
_PREFIX = re.compile(r"[A-Za-z0-9_]{0,64}")
_REQUIREMENT = re.compile(
    r"[A-Za-z0-9][A-Za-z0-9_.\-]*(\[[A-Za-z0-9_,.\-]+\])?(==[A-Za-z0-9_.!+\-]+)?"
)


class NotebookNotFoundError(RuntimeError):
    """The worker notebook could not be found or created."""


class ServiceUrlError(NotebookNotFoundError, ValueError):
    """The service answered with a URL off the Fabric API's origin; the token is not sent there.
    Also a ``ValueError``, as ``shape.scale.http.same_origin``."""


def _fabric_url(url: str, what: str) -> str:
    """``url`` when it is an ``https://`` URL on the Fabric API host, else an error: a header or
    a paging field of an answer must not send the bearer token to another host."""
    parts = urlsplit(url)
    api = urlsplit(FABRIC_API)
    if parts.scheme != "https" or parts.hostname != api.hostname or parts.port not in (None, 443):
        raise ServiceUrlError(
            f"the {what} is not a {api.hostname} URL ({parts.hostname or 'no host'}); "
            "the token is not sent there"
        )
    return url


@dataclass
class SparkRun:
    """What a submission returns: the ids Fabric gave, and where the spec is."""

    run_id: str
    spec_path: str
    notebook_item_id: str
    fabric_run_id: str


def default_requirements() -> list[str]:
    """The packages the notebook installs: this Shape's version, and its domains plugin."""
    from importlib import metadata

    version = metadata.version("sqllocks-shape")
    return [f"sqllocks-shape=={version}", f"sqllocks-shape-domains=={version}"]


def check_guid(value: str, what: str) -> str:
    if not _GUID.fullmatch(value or ""):
        raise ValueError(f"{what} must be a GUID, got {value!r}")
    return value


def build_spec(
    schema: dict[str, Any],
    *,
    seed: int,
    row_counts: Mapping[str, int],
    chunk_rows: int,
    sinks: list[str],
    table_prefix: str = "",
    scale: str | None = None,
) -> dict[str, Any]:
    """The job spec the worker notebook reads. It holds no credentials."""
    return {
        "version": 1,
        "schema": schema,
        "seed": int(seed),
        "scale": scale,
        "row_counts": {k: int(v) for k, v in row_counts.items()},
        "chunk_rows": int(chunk_rows),
        "sinks": list(sinks),
        "table_prefix": table_prefix,
    }


class FabricSparkRouter:
    """Submits generation jobs to a Fabric Spark notebook.

    ``token`` is a Fabric API bearer token (Items and Jobs). OneLake needs a token for the
    ``https://storage.azure.com`` audience: ``storage_token``, or one made with ``azure-identity``
    when that is installed.
    """

    def __init__(
        self,
        workspace_id: str,
        lakehouse_id: str,
        token: str,
        *,
        notebook_name: str = DEFAULT_NOTEBOOK,
        storage_token: str | None = None,
        table_prefix: str = "",
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        storage_token_factory: Callable[[], str] | None = None,
        requirements: Sequence[str] | None = None,
    ) -> None:
        self._workspace = check_guid(workspace_id, "workspace_id")
        self._lakehouse = check_guid(lakehouse_id, "lakehouse_id")
        if not token:
            raise ValueError("fabric_spark needs a Fabric token")
        if not _NAME.fullmatch(notebook_name):
            raise ValueError(f"invalid notebook name {notebook_name!r}")
        if not _PREFIX.fullmatch(table_prefix):
            raise ValueError("table_prefix may hold letters, digits and underscores only")
        self._http = Http(token, transport, sleep=sleep)
        self._notebook = notebook_name
        self._storage_token = storage_token
        self._storage_factory = storage_token_factory
        self._prefix = table_prefix
        self._sleep = sleep
        self._requirements = (
            list(requirements) if requirements is not None else default_requirements()
        )
        for item in self._requirements:
            if not _REQUIREMENT.fullmatch(item):
                raise ValueError(f"not a plain pip requirement: {item!r}")

    # -- OneLake --------------------------------------------------------------------------

    def _storage(self) -> str:
        if self._storage_token is None:
            if self._storage_factory is not None:
                self._storage_token = self._storage_factory()
            else:
                try:
                    from azure.identity import (
                        DefaultAzureCredential,
                    )
                except ImportError as exc:
                    raise RuntimeError(
                        "OneLake needs a storage token: pass storage_token, "
                        "or install azure-identity"
                    ) from exc
                self._storage_token = (
                    DefaultAzureCredential().get_token("https://storage.azure.com/.default").token
                )
        return self._storage_token

    def upload_spec(self, spec: Mapping[str, Any], run_id: str) -> str:
        """Write the spec to OneLake (create, append in 1 MB pieces, flush); returns its path
        relative to the Lakehouse ``Files`` area."""
        data = json.dumps(spec, default=str).encode("utf-8")
        rel = f"{SPEC_DIR}/{run_id}.json"
        base = f"{ONELAKE_DFS}/{self._workspace}/{self._lakehouse}/Files/{rel}"
        token = self._storage()
        self._http.request("PUT", f"{base}?resource=file", token=token)
        piece = 1024 * 1024
        for position in range(0, len(data), piece):
            self._http.request(
                "PATCH",
                f"{base}?action=append&position={position}",
                raw=data[position : position + piece],
                token=token,
                timeout=90.0,
            )
        self._http.request("PATCH", f"{base}?action=flush&position={len(data)}", token=token)
        return rel

    # -- the notebook ---------------------------------------------------------------------

    def _list_notebooks(self) -> list[dict[str, Any]]:
        url = f"{FABRIC_API}/workspaces/{self._workspace}/notebooks"
        found: list[dict[str, Any]] = []
        seen: set[str] = set()
        while url:
            seen.add(url)
            doc = self._http.request("GET", url).json_object()
            found.extend(doc.get("value", []))
            url = doc.get("continuationUri") or ""
            if url and not on_fabric_api(url):
                raise ServiceUrlError(  # a NotebookNotFoundError (G7-eval) and a ValueError
                    "the notebook listing names a next page off the Fabric API: "
                    f"{urlsplit(url).netloc!r}"
                )
            if url:
                _fabric_url(url, "continuation URI of the notebook list")
                same_origin(url, FABRIC_API)
                if url in seen:
                    raise NotebookNotFoundError(
                        "the notebook list repeats its continuation URI; paging does not end"
                    )
        return found

    def find_notebook(self) -> str | None:
        for item in self._list_notebooks():
            if item.get("displayName") == self._notebook:
                return str(item["id"])
        return None

    def notebook_definition(self) -> dict[str, Any]:
        """The Items API body that creates the worker notebook from the bundled template."""
        from shape.scale.notebooks import worker_notebook

        template = json.dumps(worker_notebook())
        template = (
            template.replace("__SHAPE_WORKSPACE_ID__", self._workspace)
            .replace("__SHAPE_LAKEHOUSE_ID__", self._lakehouse)
            .replace("__SHAPE_REQUIREMENTS__", " ".join(self._requirements))
        )
        platform = {
            "$schema": (
                "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/"
                "platformProperties/2.0.0/schema.json"
            ),
            "metadata": {
                "type": "Notebook",
                "displayName": self._notebook,
                "description": "Shape generation worker (made by generate --scale-mode)",
            },
            "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
        }

        def part(path: str, payload: str) -> dict[str, str]:
            return {
                "path": path,
                "payload": base64.b64encode(payload.encode("utf-8")).decode("ascii"),
                "payloadType": "InlineBase64",
            }

        return {
            "displayName": self._notebook,
            "type": "Notebook",
            "definition": {
                "format": "ipynb",
                "parts": [
                    part("notebook-content.ipynb", template),
                    part(".platform", json.dumps(platform)),
                ],
            },
        }

    def get_or_create_notebook(self) -> str:
        existing = self.find_notebook()
        if existing:
            return existing
        response = self._http.request(
            "POST",
            f"{FABRIC_API}/workspaces/{self._workspace}/items",
            body=self.notebook_definition(),
            timeout=60.0,
        )
        if response.status == 201:
            item_id = response.json_object().get("id")
            if not item_id:
                raise NotebookNotFoundError("the Items API returned 201 without an id")
            return str(item_id)
        location = response.headers.get("location", "")
        if not location:
            raise NotebookNotFoundError(
                "the Items API accepted the notebook but gave no operation URL"
            )
        if not on_fabric_api(location):
            raise ServiceUrlError(  # a NotebookNotFoundError (G7-eval) and a ValueError
                "the Items API names an operation URL off the Fabric API: "
                f"{urlsplit(location).netloc!r}"
            )
        _fabric_url(location, "operation URL (Location header)")
        for _ in range(60):
            self._sleep(2.0)
            operation = self._http.request("GET", same_origin(location, FABRIC_API)).json_object()
            state = operation.get("status", "")
            if state == "Succeeded":
                created = self.find_notebook()
                if created:
                    return created
                raise NotebookNotFoundError(f"notebook {self._notebook!r} not found after creation")
            if state == "Failed":
                raise NotebookNotFoundError(
                    f"notebook creation failed: {operation.get('error', 'unknown')}"
                )
            if state in ("Cancelled", "Canceled"):
                raise NotebookNotFoundError("notebook creation was cancelled")
        raise NotebookNotFoundError("notebook creation timed out after about 2 minutes")

    # -- submitting -----------------------------------------------------------------------

    def submit_run(self, notebook_item_id: str, spec_path: str) -> str:
        """Start the notebook; returns the Fabric run id. The spec path goes in the Spark conf,
        which reaches the session (the Jobs API ignores notebook parameters)."""
        check_guid(notebook_item_id, "notebook item id")
        url = (
            f"{FABRIC_API}/workspaces/{self._workspace}/items/{notebook_item_id}"
            "/jobs/instances?jobType=RunNotebook"
        )
        body = {
            "executionData": {
                "configuration": {
                    "conf": {
                        "spark.shape.jobSpec": spec_path,
                        "spark.shape.tablePrefix": self._prefix,
                    }
                }
            }
        }
        response = self._http.request("POST", url, body=body)
        location = response.headers.get("location", "")
        if location:
            run_id = location.rstrip("/").split("/")[-1]
        else:
            run_id = str(response.json_object().get("id", ""))
        if not run_id:
            raise RuntimeError("Fabric accepted the job but returned no run id")
        return run_id

    def submit(self, spec: Mapping[str, Any]) -> SparkRun:
        run_id = uuid.uuid4().hex
        spec_path = self.upload_spec(spec, run_id)
        notebook = self.get_or_create_notebook()
        fabric_run = self.submit_run(notebook, spec_path)
        return SparkRun(run_id, spec_path, notebook, fabric_run)

    def job_record(self, run: SparkRun, request: dict[str, Any]) -> JobRecord:
        return JobRecord(
            job_id=f"spark-{run.run_id[:8]}",
            kind="fabric_spark",
            request=request,
            fabric={
                "workspace_id": self._workspace,
                "lakehouse_id": self._lakehouse,
                "notebook_item_id": run.notebook_item_id,
                "fabric_run_id": run.fabric_run_id,
                "notebook_name": self._notebook,
                "spec_path": run.spec_path,
                "table_prefix": self._prefix,
            },
        )
