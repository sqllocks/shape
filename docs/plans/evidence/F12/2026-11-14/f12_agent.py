"""Agent-side provisioning and execution. Never prints response bodies or connection values.
Run from the extracted archive: python f12_agent.py setup|run|status|cleanup|render
Only IDs of items created by this invocation are persisted in .f12-state.json.
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = "https://api.fabric.microsoft.com/v1/"
WORKSPACE = "e90940bd-3f32-42ce-998e-ef2ddc74355b"
BASE = f"workspaces/{WORKSPACE}/"
STATE = ROOT / ".f12-state.json"
# Learn notebook-limitation: 32 MB notebook content. Decimal bytes are conservative.
NOTEBOOK_CONTENT_LIMIT_BYTES = 32_000_000
# Local conservative wire budget, NOT a documented Fabric HTTP-body limit.
HANDOFF_REQUEST_BUDGET_BYTES = 32_000_000


class ObservedFailure(Exception):
    def __init__(self, stage, status=None):
        self.stage, self.status = stage, status


def receipt(stage, **fields):
    print(json.dumps({"stage": stage, **fields}), flush=True)


def part(path, value):
    content = value if isinstance(value, str) else json.dumps(value)
    return {
        "path": path,
        "payloadType": "InlineBase64",
        "payload": base64.b64encode(content.encode()).decode(),
    }


def database_body(parent):
    mapping = [
        {"column": k, "Properties": {"path": "$." + k}}
        for k in ("id", "marker", "_shape_table", "_shape_seq")
    ]
    schema = (
        ".create table ['f12-events'] (id:long, marker:string, "
        "_shape_table:string, _shape_seq:long)\n"
    )
    schema += ".create-or-alter table ['f12-events'] ingestion json mapping 'f12-json' "
    schema += json.dumps(json.dumps(mapping)) + "\n"
    return {
        "displayName": "f12-kql",
        "definition": {
            "parts": [
                part(
                    "DatabaseProperties.json",
                    {"databaseType": "ReadWrite", "parentEventhouseItemId": parent},
                ),
                part("DatabaseSchema.kql", schema),
            ]
        },
    }


def eventstream_body(database):
    topology = {
        "sources": [{"name": "f12-input", "type": "CustomEndpoint", "properties": {}}],
        "streams": [
            {
                "name": "f12-stream",
                "type": "DefaultStream",
                "properties": {},
                "inputNodes": [{"name": "f12-input"}],
            }
        ],
        "operators": [],
        "destinations": [
            {
                "name": "f12-destination",
                "type": "Eventhouse",
                "inputNodes": [{"name": "f12-stream"}],
                "properties": {
                    "dataIngestionMode": "ProcessedIngestion",
                    "workspaceId": WORKSPACE,
                    "itemId": database,
                    "databaseName": "f12-kql",
                    "tableName": "f12-events",
                    "inputSerialization": {"type": "Json", "properties": {"encoding": "UTF8"}},
                },
            }
        ],
        "compatibilityLevel": "1.1",
    }
    return {
        "displayName": "f12-eventstream",
        "type": "Eventstream",
        "definition": {"parts": [part("eventstream.json", topology)]},
    }


def safe_records(raw):
    """Allow only expected measurement fields from our own structured exit value."""
    records = json.loads(raw)
    if not isinstance(records, list):
        raise ObservedFailure("exit_value_shape")
    stages = {
        "before_install",
        "after_install",
        "resource_limit",
        "wheel_install",
        "generation",
        "profile",
        "child_exit",
        "error",
        "memory",
        "semantic_model",
        "destination_readiness",
        "events_sent",
        "events_landed",
        "events",
    }
    numeric = {
        "cpu_count",
        "rows",
        "columns",
        "seconds",
        "parquet_bytes",
        "peak_rss_raw",
        "exit_code",
        "relationships",
        "emitter_acknowledged",
        "stream_writer_acknowledged",
        "direct_writer_accepted",
        "poll_attempts",
        "poll_seconds",
    }
    flags = {
        "hashes_verified",
        "installed",
        "source_running",
        "destination_running",
        "warning",
        "counts_match",
        "versions_match",
    }
    enums = {
        "result": {"failed", "skipped"},
        "kernel": {"python", "rust"},
        "mode": {"bounded"},
        "platform_system": {"Linux", "Windows", "Darwin"},
        "machine": {"x86_64", "aarch64", "AMD64", "arm64"},
        "path": {"/sys/fs/cgroup/cpu.max", "/sys/fs/cgroup/memory.max"},
        "peak_rss_unit": {"KiB", "bytes"},
        "destination_status": {
            "NotChecked",
            "Running",
            "Warning",
            "Stopped",
            "Paused",
            "Starting",
            "Failed",
            "Other",
        },
        "imports": {"passed"},
        "test_body": {"passed", "failed"},
    }
    output = []
    for record in records:
        if not isinstance(record, dict) or record.get("stage") not in stages:
            raise ObservedFailure("exit_value_record_shape")
        clean = {"stage": record["stage"]}
        for key, value in record.items():
            if key in numeric and type(value) in (int, float) and math.isfinite(value):
                clean[key] = value
            elif key in flags and type(value) is bool:
                clean[key] = value
            elif key in enums and isinstance(value, str) and value in enums[key]:
                clean[key] = value
            elif (
                key in ("python", "shape")
                and isinstance(value, str)
                and re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,50}", value)
            ):
                clean[key] = value
            elif (
                key == "error_type"
                and isinstance(value, str)
                and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,70}", value)
            ):
                clean[key] = value
            elif (
                key == "value"
                and record["stage"] == "resource_limit"
                and isinstance(value, str)
                and re.fullmatch(r"(?:max|[0-9]+)(?: [0-9]+)?", value)
            ):
                clean[key] = value
            elif key in ("packages", "loaded_versions") and isinstance(value, dict):
                clean[key] = {
                    k: v
                    for k, v in value.items()
                    if k in ("numpy", "pandas", "pyarrow", "semantic-link-sempy")
                    and isinstance(v, str)
                    and re.fullmatch(r"[0-9][0-9A-Za-z.+-]{0,50}", v)
                }
            elif key in ("raw_rows_by_table", "unique_rows_by_table") and isinstance(value, list):
                if all(
                    isinstance(row, list)
                    and len(row) == 2
                    and row[0] in ("f12-emitter", "f12-writer")
                    and type(row[1]) is int
                    for row in value
                ):
                    clean[key] = value
            elif key == "direct_writer_count" and isinstance(value, list):
                if all(
                    isinstance(row, list) and len(row) == 1 and type(row[0]) is int for row in value
                ):
                    clean[key] = value
            elif key == "tables" and isinstance(value, list):
                clean[key] = [
                    {
                        "table_index": i,
                        **{
                            k: v
                            for k, v in row.items()
                            if k in ("rows", "columns") and type(v) is int
                        },
                    }
                    for i, row in enumerate(value)
                    if isinstance(row, dict)
                ]
        output.append(clean)
    return output


def notebook_create_body(notebook):
    return {
        "displayName": "f12-owner-qualification",
        "type": "Notebook",
        "definition": {
            "format": "ipynb",
            "parts": [part("notebook-content.ipynb", notebook)],
        },
    }


def check_notebook_size(notebook):
    content_bytes = len(json.dumps(notebook).encode())
    body = notebook_create_body(notebook)
    payload_bytes = len(body["definition"]["parts"][0]["payload"].encode("ascii"))
    request_bytes = len(json.dumps(body).encode())
    receipt(
        "notebook_size_preflight",
        notebook_content_bytes=content_bytes,
        inline_base64_bytes=payload_bytes,
        create_request_bytes=request_bytes,
        documented_content_budget_bytes=NOTEBOOK_CONTENT_LIMIT_BYTES,
        local_request_budget_bytes=HANDOFF_REQUEST_BUDGET_BYTES,
    )
    if content_bytes > NOTEBOOK_CONTENT_LIMIT_BYTES:
        raise ObservedFailure("notebook_content_limit_exceeded")
    if request_bytes > HANDOFF_REQUEST_BUDGET_BYTES:
        raise ObservedFailure("notebook_local_request_budget_exceeded")
    return body


def render():
    manifest = json.loads((ROOT / "handoff-build.json").read_text())
    payloads = {}
    for artifact in manifest["artifacts"]:
        data = (ROOT / artifact["name"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != artifact["sha256"]:
            raise ObservedFailure("wheel_hash_mismatch")
        payloads[artifact["name"]] = base64.b64encode(data).decode()
    notebook = json.loads((ROOT / "f12-owner-qualification.ipynb").read_text())
    source = "".join(notebook["cells"][0]["source"])
    needle = "F12_WHEEL_PAYLOADS = {}"
    if source.count(needle) != 1:
        raise ObservedFailure("wheel_injection_marker")
    notebook["cells"][0]["source"] = source.replace(
        needle, "F12_WHEEL_PAYLOADS = " + repr(payloads)
    ).splitlines(True)
    check_notebook_size(notebook)
    return notebook


class Agent:
    def __init__(self, *, access_token=None, token_provider=None):
        self.state = json.loads(STATE.read_text()) if STATE.exists() else {"items": []}
        self._token_provider = None
        # Explicit caller credentials take precedence; invalid supplied tokens never
        # silently fall back to a service principal with a different identity.
        if access_token is not None:
            self.token = self._validate_token(access_token)
            self.auth_source = "caller_token"
            return
        if token_provider is not None:
            if not callable(token_provider):
                raise ObservedFailure("caller_token_provider_invalid")
            self._token_provider = token_provider
            self.token = self._provider_token()
            self.auth_source = "caller_token_provider"
            return
        if "FABRIC_ACCESS_TOKEN" in os.environ:
            self.token = self._validate_token(os.environ["FABRIC_ACCESS_TOKEN"])
            self.auth_source = "caller_token_environment"
            return
        self.auth_source = "service_principal"
        try:
            payload = urllib.parse.urlencode(
                {
                    "grant_type": "client_credentials",
                    "client_id": os.environ["FABRIC_CLIENT_ID"],
                    "client_secret": os.environ["FABRIC_CLIENT_SECRET"],
                    "scope": "https://api.fabric.microsoft.com/.default",
                }
            ).encode()
            url = (
                "https://login.microsoftonline.com/"
                + os.environ["FABRIC_TENANT_ID"]
                + "/oauth2/v2.0/token"
            )
            with urllib.request.urlopen(
                urllib.request.Request(url, data=payload), timeout=60
            ) as response:
                self.token = self._validate_token(json.load(response)["access_token"])
        except Exception:
            raise ObservedFailure("authentication") from None

    @staticmethod
    def _validate_token(token):
        if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
            raise ObservedFailure("caller_token_invalid")
        return token

    def _provider_token(self):
        try:
            return self._validate_token(self._token_provider())
        except Exception:
            raise ObservedFailure("caller_token_provider_failed") from None

    def save(self):
        STATE.write_text(json.dumps(self.state, indent=2) + "\n")
        STATE.chmod(0o600)

    def call(self, method, path, body=None):
        url = path if path.startswith("https://") else API + path
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "api.fabric.microsoft.com":
            raise ObservedFailure("response_origin_guard")
        token = self._provider_token() if self._token_provider else self.token
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return (
                    response.status,
                    dict((k.lower(), v) for k, v in response.headers.items()),
                    json.loads(response.read() or "{}"),
                )
        except urllib.error.HTTPError as error:
            # Never print/read remote diagnostic bodies: they can echo protected values.
            raise ObservedFailure("fabric_http", error.code) from None
        except Exception:
            raise ObservedFailure("fabric_transport") from None

    def create(self, collection, body, kind):
        if not body["displayName"].startswith("f12-"):
            raise ObservedFailure("create_name_guard")
        status, headers, data = self.call("POST", BASE + collection, body)
        receipt("create_" + kind.lower(), http_status=status)
        if status == 202:
            location = headers["location"]
            for _ in range(120):
                time.sleep(max(1, int(headers.get("retry-after", "5"))))
                _, headers, operation = self.call("GET", location)
                if operation.get("status") == "Failed":
                    raise ObservedFailure("create_operation_failed")
                if operation.get("status") == "Succeeded":
                    _, _, data = self.call("GET", location + "/result")
                    break
            else:
                raise ObservedFailure("create_operation_timeout")
        if not data.get("id"):
            raise ObservedFailure("create_result_missing")
        self.state["items"].append({"id": data["id"], "type": kind})
        self.save()
        return data["id"]

    def setup(self):
        _, _, listed = self.call("GET", BASE + "items")
        if listed.get("continuationUri") or listed.get("continuationToken"):
            raise ObservedFailure("preflight_pagination_requires_inspection")
        if any(x["displayName"].startswith("f12-") for x in listed.get("value", [])):
            raise ObservedFailure("existing_f12_items_no_reuse")
        if self.state["items"]:
            raise ObservedFailure("existing_ledger_no_reuse")
        eh = self.create("eventhouses", {"displayName": "f12-eventhouse"}, "Eventhouse")
        # A service-created default database belongs to this new parent only.
        _, _, listed = self.call("GET", BASE + "kqlDatabases")
        if listed.get("continuationUri") or listed.get("continuationToken"):
            raise ObservedFailure("database_pagination_requires_inspection")
        for item in listed.get("value", []):
            _, _, details = self.call("GET", BASE + "kqlDatabases/" + item["id"])
            if details.get("properties", {}).get("parentEventhouseItemId") == eh:
                if not details["displayName"].startswith("f12-"):
                    raise ObservedFailure("automatic_child_name_guard")
                self.state["items"].append({"id": item["id"], "type": "KQLDatabase"})
                self.save()
        db = self.create("kqlDatabases", database_body(eh), "KQLDatabase")
        es = self.create("items", eventstream_body(db), "Eventstream")
        self.state.update(database_id=db, eventstream_id=es)
        self.save()
        self.ready()
        receipt("setup", result="ready")

    def ready(self):
        es, db = self.state["eventstream_id"], self.state["database_id"]
        for attempt in range(30):
            _, _, topology = self.call("GET", BASE + "eventstreams/" + es + "/topology")
            destinations = [
                x for x in topology.get("destinations", []) if x.get("name") == "f12-destination"
            ]
            if not destinations:
                time.sleep(5)
                continue
            destination = destinations[0]
            url = BASE + "eventstreams/" + es + "/destinations/" + destination["id"]
            _, _, details = self.call("GET", url)
            status = details.get("status")
            if status == "Warning":
                raise ObservedFailure("destination_warning_no_events_sent")
            properties = details.get("properties", {})
            if (
                properties.get("dataIngestionMode") != "ProcessedIngestion"
                or properties.get("itemId") != db
            ):
                raise ObservedFailure("destination_mode_or_item_mismatch")
            if (
                properties.get("tableName") != "f12-events"
                or properties.get("inputSerialization", {}).get("type") != "Json"
            ):
                raise ObservedFailure("destination_json_mismatch")
            if status == "Running" and not details.get("error"):
                sources = [x for x in topology.get("sources", []) if x.get("name") == "f12-input"]
                if len(sources) == 1 and sources[0].get("status") == "Running":
                    receipt(
                        "readiness", source_running=True, destination_running=True, warning=False
                    )
                    return
            if attempt == 0:
                self.call("POST", url + "/resume", {"startType": "Now"})
                receipt("destination_resume", result="accepted")
            time.sleep(5)
        raise ObservedFailure("destination_or_source_not_running")

    def run(self):
        if self.state.get("job_location"):
            raise ObservedFailure("job_already_submitted_use_status")
        # Fail before readiness requests or notebook creation if the payload is too big.
        notebook = render()
        body = notebook_create_body(notebook)
        self.ready()
        item = self.create("items", body, "Notebook")
        parameters = [{"name": "F12_NONINTERACTIVE", "value": True, "type": "Boolean"}]
        for key, value in (
            ("F12_EVENTSTREAM_ID", self.state["eventstream_id"]),
            ("F12_KQL_DATABASE_ID", self.state["database_id"]),
        ):
            parameters.append({"name": key, "value": value, "type": "Text"})
        status, headers, _ = self.call(
            "POST",
            BASE + "notebooks/" + item + "/jobs/execute/instances?beta=false",
            {
                "executionData": {"compute": "Jupyter", "computeConfiguration": {"numCores": 2}},
                "parameters": parameters,
            },
        )
        self.state["job_location"] = headers["location"]
        self.save()
        receipt(
            "submit_python_job", http_status=status, requested_vcores=2, environment_attached=False
        )
        # Observe Retry-After before the first status poll; status is a separate command.
        self.state["not_before"] = time.time() + int(headers.get("retry-after", "60"))
        self.save()

    def exit_value_url(self):
        location = urllib.parse.urlsplit(self.state["job_location"])
        if location.scheme != "https" or location.netloc != "api.fabric.microsoft.com":
            raise ObservedFailure("response_origin_guard")
        # Location commonly uses the generic item job endpoint. Exit values use
        # the notebook-specific execute endpoint, not that path with a query added.
        prefix = "/v1/" + BASE
        tail = location.path.removeprefix(prefix)
        if not location.path.startswith(prefix):
            raise ObservedFailure("job_location_path_guard")
        match = re.fullmatch(
            r"(?:items/([^/]+)/jobs/instances/([^/]+)|"
            r"notebooks/([^/]+)/jobs/(?:execute/)?instances/([^/]+))",
            tail,
        )
        if not match:
            raise ObservedFailure("job_location_path_guard")
        notebook_id = match[1] or match[3]
        job_id = match[2] or match[4]
        if not any(
            item["id"] == notebook_id and item["type"] == "Notebook" for item in self.state["items"]
        ):
            raise ObservedFailure("job_location_ownership_guard")
        return (
            API
            + BASE
            + "notebooks/"
            + notebook_id
            + "/jobs/execute/instances/"
            + job_id
            + "?beta=true"
        )

    def status(self):
        if time.time() < self.state.get("not_before", 0):
            receipt("job", result="wait_retry_after")
            return
        try:
            _, _, data = self.call("GET", self.exit_value_url())
        except ObservedFailure as error:
            if error.stage != "fabric_http":
                raise
            receipt(
                "job_exit_value",
                available=False,
                beta_endpoint=True,
                http_status=error.status,
                result="owner_safe_output_required",
            )
            raise
        status = data.get("status")
        allowed = {"Completed", "Failed", "Cancelled", "InProgress", "NotStarted", "Deduped"}
        receipt("job", status=status if status in allowed else "Other")
        # Exit value can be returned by Fabric, but never relay arbitrary remote text.
        # Agent/Owner reads safe notebook output if exitValue is absent.
        properties = data.get("properties")
        exit_value = data.get("exitValue")
        if exit_value is None and isinstance(properties, dict):
            exit_value = properties.get("exitValue")
        available = exit_value is not None
        nonempty = isinstance(exit_value, str) and bool(exit_value)
        receipt(
            "job_exit_value",
            available=available,
            beta_endpoint=True,
            nonempty=nonempty,
            result="available" if nonempty else "owner_safe_output_required",
        )
        if nonempty:
            try:
                records = safe_records(exit_value)
            except Exception:
                receipt(
                    "job_exit_value",
                    available=True,
                    parsed=False,
                    result="owner_safe_output_required",
                )
                raise ObservedFailure("job_exit_value_unusable") from None
            for record in records:
                print(json.dumps(record), flush=True)

    def cleanup(self):
        if self.state.get("job_location"):
            _, _, job = self.call("GET", self.state["job_location"])
            if job.get("status") not in ("Completed", "Failed", "Cancelled", "Deduped"):
                raise ObservedFailure("active_job_stop_before_cleanup")
        for item in list(reversed(self.state["items"])):
            _, _, details = self.call("GET", BASE + "items/" + item["id"])
            if not details["displayName"].startswith("f12-") or details["type"] != item["type"]:
                raise ObservedFailure("delete_name_or_type_guard")
            self.call("DELETE", BASE + "items/" + item["id"])
            self.state["items"].remove(item)
            self.save()
            receipt("delete_owned_item", result="deleted")
        receipt("cleanup", remaining_owned_items=len(self.state["items"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("setup", "run", "status", "cleanup", "render"))
    args = parser.parse_args()
    try:
        if args.action == "render":
            (ROOT / "f12-rendered.ipynb").write_text(json.dumps(render()))
            receipt("render", result="created")
            return
        getattr(Agent(), args.action)()
    except ObservedFailure as error:
        receipt(error.stage, result="failed", http_status=error.status)
        return 1
    except Exception as error:
        receipt("agent", result="failed", error_type=type(error).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
