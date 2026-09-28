"""Canonical RC .shape artifact contract."""

from __future__ import annotations

import hashlib
import json

from shape.privacy.policy import LEVELS
from shape.security import enforce_no_secrets, validate_structure

from .io import canonical_json, read_artifact, write_artifact

FORMAT = "shape"
FORMAT_VERSION = 1


def _classification(x):
    x = str(x).upper()
    if x not in LEVELS:
        raise ValueError("unknown classification")
    return x


def write_shape(
    path, shape: dict, *, name="shape", fidelity="gold", classification="PUBLIC", metadata=None
):
    classification = _classification(classification)
    validate_structure(shape)
    enforce_no_secrets(shape)
    metadata = metadata or {}
    validate_structure(metadata)
    enforce_no_secrets(metadata)
    body = canonical_json(shape)
    content_id = hashlib.sha256(body).hexdigest()
    manifest = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "name": str(name),
        "shape_content_id": content_id,
        "fidelity": str(fidelity),
        "classification": classification,
        "metadata": metadata,
    }
    write_artifact(path, manifest, {"shape.json": body})
    return content_id


def read_shape(path):
    m, c = read_artifact(path)
    if m.get("format") != FORMAT:
        raise ValueError("not a Shape artifact")
    if not isinstance(m.get("format_version"), int):
        raise ValueError("invalid Shape format version")
    if int(m["format_version"]) < 1 or int(m["format_version"]) > FORMAT_VERSION:
        raise ValueError("unsupported Shape artifact version")
    _classification(m.get("classification", "PUBLIC"))
    obj = json.loads(c["shape.json"])
    validate_structure(obj)
    expected = m.get("shape_content_id")
    actual = hashlib.sha256(c["shape.json"]).hexdigest()
    if not isinstance(expected, str) or expected != actual:
        raise ValueError("Shape content identity mismatch")
    return m, obj
