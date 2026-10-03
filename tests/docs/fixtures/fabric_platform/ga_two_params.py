"""Fixture: a listed GA path built through an f-string with two parameters."""

from shape.scale.http import FABRIC_API, Http


def run(http: Http, ws: str, item: str) -> None:
    http.request("GET", f"{FABRIC_API}/workspaces/{ws}/gizmos/{item}")
