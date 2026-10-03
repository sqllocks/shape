"""Fixture: calls an endpoint the fixture inventory does not list."""

from shape.scale.http import FABRIC_API, Http


def run(http: Http, workspace_id: str) -> None:
    http.request("POST", f"{FABRIC_API}/workspaces/{workspace_id}/unlistedthings")
