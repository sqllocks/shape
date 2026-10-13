"""Fabric deploy failures retain the service reason without exposing its credentials."""

from __future__ import annotations

import json

import pytest
from shape_fabric import commands
from shape_fabric.fabric_api import FabricApi, FabricApiError
from shape_fabric.testing import WORKSPACE_ID, FakeIdentity

from shape.cli.main import main
from shape.scale.http import HttpResponse


def test_deploy_reports_code_and_message_even_after_a_long_request_id(monkeypatch, capsys):
    FakeIdentity().install(monkeypatch)
    document = {
        "requestId": "r" * 500,
        "errorCode": "InvalidNotebookDefinition",
        "message": "Notebook definition is invalid: client_secret=private-value",
    }
    monkeypatch.setattr(
        commands,
        "API_TRANSPORT",
        lambda *args: HttpResponse(400, json.dumps(document).encode()),
    )
    assert main(["deploy-notebook", "retail", "--workspace", WORKSPACE_ID]) == 1
    error = capsys.readouterr().err
    assert "HTTP 400" in error and "InvalidNotebookDefinition" in error
    assert "Notebook definition is invalid" in error
    assert "private-value" not in error


def test_deploy_async_failure_reports_http_status_code_and_message():
    def transport(method, url, headers, body, timeout):
        if method == "POST":
            return HttpResponse(
                202, headers={"location": "https://api.fabric.microsoft.com/v1/operations/o"}
            )
        return HttpResponse(
            200,
            json.dumps(
                {
                    "status": "Failed",
                    "error": {
                        "errorCode": "InvalidNotebookDefinition",
                        "message": "bad definition; token=private-value",
                    },
                }
            ).encode(),
        )

    api = FabricApi(lambda scope: "access-token", transport=transport, sleep=lambda seconds: None)
    with pytest.raises(FabricApiError) as failure:
        api.create_item(WORKSPACE_ID, {"displayName": "n", "type": "Notebook"})
    message = str(failure.value)
    assert "HTTP 200" in message and "InvalidNotebookDefinition" in message
    assert "bad definition" in message and "private-value" not in message
