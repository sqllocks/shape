"""Reference-asset integrity verification."""

from __future__ import annotations

import hashlib

from .base import ReferenceAsset


def verify_asset(asset: ReferenceAsset, path) -> bool:
    if not asset.sha256:
        raise ValueError("asset has no declared sha256")
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1024 * 1024), b""):
            h.update(b)
    return h.hexdigest().lower() == asset.sha256.lower()
