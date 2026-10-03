"""Fixture: creates an item type the fixture inventory does not list."""


def body(name: str) -> dict[str, str]:
    return {"displayName": name, "type": "KQLDashboard"}
