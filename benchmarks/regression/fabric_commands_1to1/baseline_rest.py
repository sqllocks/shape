"""Runs the baseline's ``deploy-notebook`` and ``setup-fabric`` against a fake Fabric service, in
the baseline venv, and prints the conversation as JSON (P6-07c).

    "$REFENGINE_PY" baseline_rest.py deploy|setup [command-line options...]

``azure.identity`` and ``requests`` are replaced for the run: no network, no sign-in. The output
is ``{"exit": N, "stdout": "...", "calls": [{"method", "path", "body"}...]}`` with the base64
parts of an item definition decoded, so the two tools' requests can be compared as data.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import types
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
import _refpkg  # noqa: E402

WORKSPACE = "11111111-1111-4111-8111-111111111111"
CALLS: list[dict] = []


class _Response:
    def __init__(self, status: int, doc: dict) -> None:
        self.status_code = status
        self._doc = doc
        self.text = json.dumps(doc)

    def json(self) -> dict:
        return self._doc

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _decode(body):
    if body and isinstance(body, dict) and "definition" in body:
        body = json.loads(json.dumps(body))
        for part in body["definition"].get("parts", []):
            part["payload"] = base64.b64decode(part["payload"]).decode("utf-8")
    return body


def _path(url: str) -> str:
    return url.split("fabric.microsoft.com", 1)[1].split("?", 1)[0]


def get(url, headers=None, **kw):
    CALLS.append({"method": "GET", "path": _path(url), "body": None})
    if _path(url) == "/v1/workspaces":
        if os.environ.get("FAKE_PAGED"):  # the workspace is on the second page of the listing
            if "continuationToken" not in url:
                return _Response(
                    200,
                    {
                        "value": [
                            {"id": "22222222-2222-4222-8222-222222222222", "displayName": "Other"}
                        ],
                        "continuationToken": "1",
                    },
                )
        return _Response(200, {"value": [{"id": WORKSPACE, "displayName": "Demo"}]})
    return _Response(200, {"value": []})


def post(url, headers=None, json=None, **kw):  # noqa: A002
    CALLS.append({"method": "POST", "path": _path(url), "body": _decode(json)})
    if os.environ.get("FAKE_ACCEPTED"):  # 202: the item is made later; there is no body yet
        return _Response(202, {})
    item = {"id": "00000001-0000-4000-8000-000000000000", "displayName": json["displayName"]}
    return _Response(201, {**item, "type": json["type"]})


class _Credential:
    def __init__(self, *a, **kw) -> None:
        pass

    def get_token(self, *scopes):
        return types.SimpleNamespace(token="fake-token-for-the-comparison", expires_on=0)


def main(argv: list[str]) -> int:
    import requests

    requests.get, requests.post = get, post
    azure = types.ModuleType("azure")
    identity = types.ModuleType("azure.identity")
    identity.AzureCliCredential = identity.DeviceCodeCredential = _Credential
    azure.identity = identity
    sys.modules.update({"azure": azure, "azure.identity": identity})
    from click.testing import CliRunner

    cli = _refpkg.mod("cli").main

    command = {"deploy": "deploy-notebook", "setup": "setup-fabric"}[argv[0]]
    result = CliRunner().invoke(cli, [command, *argv[1:]])
    print(json.dumps({"exit": result.exit_code, "stdout": result.output, "calls": CALLS}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
