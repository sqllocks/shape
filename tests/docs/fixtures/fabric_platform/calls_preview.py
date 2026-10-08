"""Fixture: calls an endpoint the fixture inventory lists as preview."""

from shape.scale.http import FABRIC_API, Http


def run(http: Http, workspace_id: str) -> None:
    http.request("GET", f"{FABRIC_API}/workspaces/{workspace_id}/previewthings")
