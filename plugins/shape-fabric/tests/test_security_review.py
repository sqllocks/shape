"""P7-04 security review regression test for the Fabric plugin (moved here from
tests/security/test_p7_04_review.py so it runs with the plugin installed instead of being skipped
by the core suite). It failed before its fix."""

from __future__ import annotations

import json
import re

import pyarrow as pa
import pytest

pytestmark = pytest.mark.security


def test_kql_mapping_literal_escapes_backslashes():
    from shape_fabric.eventhouse import create_mapping_command

    name = 'x", "path": "$[\\"_shape_seq\\"]", "datatype": "string"}, {"column": "zz'
    cmd = create_mapping_command("t", pa.schema([pa.field(name, pa.int64())]))
    literal = cmd.split("ingestion json mapping 'shape_json' '", 1)[1][:-1]
    # Decode the KQL single-quoted literal the way the service does, then parse the JSON.
    decoded = re.sub(r"\\(.)", r"\1", literal)
    cols = json.loads(decoded)
    # still one column whose JSON path is the event's own key (the injection stayed inside the
    # string); the column itself is a valid Kusto name (BF-223: `"` is not allowed in one)
    assert len(cols) == 1 and json.loads(cols[0]["path"][1:].strip("[]")) == name
    assert re.fullmatch(r"[\w .-]+", cols[0]["column"]) and '"' not in cols[0]["column"]


def test_434_an_operation_url_on_another_host_gets_no_token():
    """A 202 answer whose Location names another host is refused before any request goes there."""
    from shape_fabric.fabric_api import FabricApi, FabricApiError, tuple_transport

    calls: list[tuple[str, str, dict[str, str]]] = []

    def answer(method, url, headers, body, timeout):
        calls.append((method, url, headers))
        if method == "POST":
            return 202, {"Location": "https://attacker.example/op"}, b""
        return 200, {}, b'{"status": "Succeeded", "value": []}'

    api = FabricApi(
        lambda scope: "SECRET-TOKEN", transport=tuple_transport(answer), sleep=lambda s: None
    )
    with pytest.raises(FabricApiError, match="attacker.example"):
        api.create_item("ws", {"displayName": "nb", "type": "Notebook"})
    assert [u for _, u, _ in calls] == ["https://api.fabric.microsoft.com/v1/workspaces/ws/items"]


# ---- SEC-high (#411): secrets kept in tapes ------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "secret"),
    [
        ('{"client_secret": "S3cr3tValue", "password": "hunter2"}', ("S3cr3tValue", "hunter2")),
        ("Password='hunter2 two';", ("hunter2", "two")),
        ('pwd="x y z";Server=s', ("x y z",)),
        ("password=my secret phrase", ("my secret phrase", "phrase")),
        ("PWD={ab}}cdTOPSECRET;x};Server=s", ("cdTOPSECRET", "ab")),
        ("{'secret': 'it\\'s me'}", ("it\\'s me", "me'")),
    ],
)
def test_411_the_scrubber_redacts_quoted_braced_and_spaced_values(text, secret):
    from shape_fabric import recording as r

    scrubbed = r.scrub(text)
    for part in secret:
        assert part not in scrubbed, scrubbed
    assert r.find_secrets(scrubbed) == [] and r.find_secrets(text) != []
    assert r.scrub(scrubbed) == scrubbed  # idempotent: replay scrubs the request again


def test_411_a_login_connection_string_leaves_nothing_after_a_closing_brace():
    from shape_fabric import recording as r
    from shape_fabric.auth import connection_string_with_login

    cs = connection_string_with_login("Driver={ODBC Driver 18};Server=x", "u", "ab}cdTOPSECRET;x")
    scrubbed = r.scrub(cs)
    assert "TOPSECRET" not in scrubbed and "Server=x" in scrubbed and "UID={u}" in scrubbed
    assert r.find_secrets(scrubbed) == []


def test_411_the_scrubber_keeps_json_valid_and_other_text_unchanged():
    from shape_fabric import recording as r

    doc = json.loads(r.scrub('{"password": "hunter2", "name": "kept"}'))
    assert doc == {"password": "<redacted>", "name": "kept"}
    plain = "Server=tcp:x,1433;Database=db;Encrypt=yes;sig is not a key; secretName=keep"
    assert r.scrub(plain) == plain


def test_411_a_tape_holding_a_secret_is_refused_on_save_and_on_load(tmp_path):
    from shape_fabric import recording as r

    doc = {"format": r.FORMAT, "channel": "odbc", "scenario": "x", "source": "s", "result": None}
    doc["steps"] = [{"request": {"op": "execute", "sql": 'x {"password": "hunter2"}'}}]
    with pytest.raises(r.RecordingError, match="secret"):
        r.save(tmp_path / "a.json", doc)
    assert not (tmp_path / "a.json").exists()
    (tmp_path / "b.json").write_text(json.dumps(doc), encoding="utf-8")  # written by hand
    with pytest.raises(r.RecordingError, match="secret"):
        r.load(tmp_path / "b.json")


# ---- SEC-high (#275): the Kusto transport follows no redirect off its origin --------------------


def test_275_the_kusto_transport_does_not_send_the_token_to_a_redirect_target():
    import http.server
    import threading

    from shape_fabric.kusto import urllib_transport

    got: list[dict[str, str]] = []

    class Target(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            got.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        do_POST = do_GET  # noqa: N815

        def log_message(self, *args):
            pass

    target = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Target)
    stolen = f"http://127.0.0.1:{target.server_address[1]}/stolen"

    class Redirect(Target):
        def do_GET(self):  # noqa: N802
            self.send_response(302)
            self.send_header("Location", stolen)
            self.end_headers()

        do_POST = do_GET  # noqa: N815

    origin = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for server in (target, origin):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{origin.server_address[1]}/v1/rest/mgmt"
        status, _, _ = urllib_transport("POST", url, {"Authorization": "Bearer SECRET"}, b"{}", 5)
    finally:
        for server in (target, origin):
            server.shutdown()
            server.server_close()
    assert status == 302 and got == []
