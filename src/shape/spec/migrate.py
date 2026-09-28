"""Version migrations for declarative contracts."""

from __future__ import annotations


def migrate_dict(obj, target=1):
    current = int(obj.get("version", 1))
    if current > target:
        raise ValueError("downgrade is not supported")
    out = dict(obj)
    while current < target:
        raise ValueError(f"no migration registered from version {current}")
    return out
