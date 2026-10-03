"""Item 2 with the OpenLineage client installed: events, the file and HTTP transports."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from shape_integrations import lineage
from shape_integrations.testing import write_manifest

pytest.importorskip("openlineage.client", reason="needs the 'openlineage' extra")
pytest.importorskip("jsonschema", reason="needs jsonschema (tests/requirements.txt)")
from jsonschema import Draft202012Validator  # noqa: E402
from referencing import Registry, Resource  # noqa: E402

pytestmark = pytest.mark.integration

DATA = Path(__file__).parent / "data" / "openlineage"
FACET_SCHEMA = Path(lineage.__file__).parent / "schemas" / "shape-run-facet.json"


def run(*argv: str) -> int:
    from shape.plugins.cli import run_command
    from shape.plugins.host import default_host

    return run_command(default_host(), "lineage", list(argv))


def _registry() -> Registry:
    docs = [
        json.loads((DATA / "OpenLineage-2-0-2.json").read_text()),
        json.loads((DATA / "SchemaDatasetFacet-1-2-0.json").read_text()),
        json.loads(FACET_SCHEMA.read_text()),
    ]
    return Registry().with_resources([(d["$id"], Resource.from_contents(d)) for d in docs])


def _validator(schema_id: str, fragment: str) -> Draft202012Validator:
    registry = _registry()
    return Draft202012Validator({"$ref": f"{schema_id}#/$defs/{fragment}"}, registry=registry)


def validate_event(event: dict) -> None:
    _validator("https://openlineage.io/spec/2-0-2/OpenLineage.json", "RunEvent").validate(event)
    for ds in event["outputs"]:
        schema = ds["facets"].get("schema")
        if schema is not None:
            _validator(
                "https://openlineage.io/spec/facets/1-2-0/SchemaDatasetFacet.json",
                "SchemaDatasetFacet",
            ).validate(schema)
    _validator(
        "https://github.com/sqllocks/shape/blob/main/plugins/shape-integrations/src/"
        "shape_integrations/schemas/shape-run-facet.json",
        "ShapeRunFacet",
    ).validate(event["run"]["facets"]["shape"])


def read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_events_validate_against_the_openlineage_schema(manifest, tmp_path):
    out = tmp_path / "events.ndjson"
    assert run("emit", str(manifest), "--to", f"file://{out}") == 0
    events = read_events(out)
    assert [e["eventType"] for e in events] == ["START", "COMPLETE"]
    for e in events:
        validate_event(e)
        assert e["job"] == {"namespace": "shape", "name": "shape.retail", "facets": {}}
    assert events[0]["run"]["runId"] == events[1]["run"]["runId"]


def test_a_manifest_without_files_or_tuple_still_validates(tmp_path):
    m = write_manifest(tmp_path / "r", with_files=False, with_repro=False)
    out = tmp_path / "e.ndjson"
    assert run("emit", str(m), "--to", f"file://{out}") == 0
    for e in read_events(out):
        validate_event(e)
        assert all("schema" not in ds["facets"] for ds in e["outputs"])
        assert "reproducibility" not in e["run"]["facets"]["shape"]


def test_a_failed_run_ends_with_fail(tmp_path):
    out = tmp_path / "e.ndjson"
    assert (
        run("emit", str(write_manifest(tmp_path / "r", failed=True)), "--to", f"file://{out}") == 0
    )
    assert [e["eventType"] for e in read_events(out)] == ["START", "FAIL"]


def test_the_schema_facet_lists_the_columns_and_types(manifest, tmp_path):
    out = tmp_path / "e.ndjson"
    run("emit", str(manifest), "--to", f"file://{out}", "--namespace", "ns1")
    event = read_events(out)[0]
    by_name = {ds["name"]: ds for ds in event["outputs"]}
    assert {ds["namespace"] for ds in event["outputs"]} == {"ns1"}
    fields = by_name["customer"]["facets"]["schema"]["fields"]
    assert [(f["name"], f["type"]) for f in fields] == [("id", "int64"), ("email", "string")]


def test_file_destination_appends_ndjson(manifest, tmp_path):
    out = tmp_path / "deep" / "dir" / "events.ndjson"
    assert run("emit", str(manifest), "--to", f"file://{out}") == 0
    assert run("emit", str(manifest), "--to", f"file://{out}") == 0
    lines = out.read_text().splitlines()
    assert len(lines) == 4
    assert all(json.loads(line) for line in lines)


def test_file_destination_that_cannot_be_written_exits_1(manifest, tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory")
    assert run("emit", str(manifest), "--to", f"file://{blocker}/events.ndjson") == 1
    assert "cannot write the events" in capsys.readouterr().err


class _Server:
    """A local HTTP endpoint that records what it receives and answers with ``status``."""

    def __init__(self, status: int = 200, location: str | None = None) -> None:
        self.requests: list[tuple[dict[str, str], bytes]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                outer.requests.append((dict(self.headers), body))
                self.send_response(status)
                if location:
                    self.send_header("Location", location)
                self.end_headers()
                self.wfile.write(b"echo: " + self.headers.get("Authorization", "").encode())

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/api/v1/lineage"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> _Server:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def test_http_posts_one_event_each_with_the_bearer_token(manifest, monkeypatch, capsys):
    monkeypatch.setenv("SHAPE_TEST_TOKEN", "s3cret-token-value")
    with _Server() as srv:
        code = run("emit", str(manifest), "--to", srv.url, "--token-env", "SHAPE_TEST_TOKEN")
    assert code == 0
    assert len(srv.requests) == 2
    for headers, body in srv.requests:
        assert headers["Authorization"] == "Bearer s3cret-token-value"
        assert headers["Content-Type"] == "application/json"
        validate_event(json.loads(body))
    assert [json.loads(b)["eventType"] for _, b in srv.requests] == ["START", "COMPLETE"]
    captured = capsys.readouterr()
    assert "s3cret-token-value" not in captured.out + captured.err


def test_http_without_a_token_sends_no_authorization_header(manifest):
    with _Server() as srv:
        assert run("emit", str(manifest), "--to", srv.url) == 0
    assert all("Authorization" not in h for h, _ in srv.requests)


def test_http_error_exits_1_with_the_status_and_without_the_token(manifest, monkeypatch, capsys):
    monkeypatch.setenv("SHAPE_TEST_TOKEN", "s3cret-token-value")
    with _Server(status=503) as srv:
        code = run("emit", str(manifest), "--to", srv.url, "--token-env", "SHAPE_TEST_TOKEN")
    assert code == 1
    err = capsys.readouterr().err
    assert "503" in err and "s3cret-token-value" not in err
    assert len(srv.requests) == 1  # stops at the first refused event


def test_a_redirect_is_an_error_and_the_token_is_not_forwarded(manifest, monkeypatch, capsys):
    monkeypatch.setenv("SHAPE_TEST_TOKEN", "s3cret-token-value")
    with _Server() as target, _Server(status=307, location=target.url) as srv:
        code = run("emit", str(manifest), "--to", srv.url, "--token-env", "SHAPE_TEST_TOKEN")
    assert code == 1
    assert "307" in capsys.readouterr().err
    assert target.requests == []


def test_connection_refused_exits_1(manifest, capsys):
    with _Server() as srv:
        url = srv.url
    assert run("emit", str(manifest), "--to", url) == 1
    assert "cannot reach" in capsys.readouterr().err
