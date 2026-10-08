"""The .shape artifact, format version 2 (P1-09).

A .shape file is a zip archive of a manifest and one component, ``shape.json``: the Shape model
v2 (``shape/schemas/shape-v2.schema.json``) as JSON with NaN, infinity and tuples written as
explicit tagged objects (``codec``). Format version 1 files (a v1 capture in ``shape.json``) are
read through the migrator; nothing writes version 1 any more.

``write_model``/``read_model`` speak the v2 model. ``write_shape``/``read_shape`` are the
entry points the v1 consumers still use: ``write_shape`` takes a v2 model or a v1 capture (it is
migrated), ``read_shape`` returns the v1 document of a migrated capture (``legacy_view``).
Contracts, drift, diff, quality and query read the v2 model itself (P1-10); generation, privacy,
streaming and the registry still read the v1 document, and these two names stay until they
move.
"""

from __future__ import annotations

import hashlib
from typing import Any, cast

from shape import compat
from shape.privacy.policy import LEVELS
from shape.security import SecurityError, enforce_no_secrets, validate_structure
from shape.spec.migrate import legacy_view, to_model
from shape.spec.model import ModelError

from . import codec
from .io import ArtifactError, ArtifactRead, read_artifact, write_artifact
from .migrate import MIGRATIONS

FORMAT = "shape"
FORMAT_VERSION = 2
COMPONENT = "shape.json"

MIGRATIONS.register(
    1, 2, "capture-v1-to-model-v2", lambda m, s: (m, to_model(s, str(m.get("name") or "") or None))
)


def _classification(x: Any) -> str:
    x = str(x).upper()
    if x not in LEVELS:
        raise ValueError("unknown classification")
    return cast(str, x)


def write_model(
    path: Any,
    shape: Any,
    *,
    name: str = "shape",
    fidelity: str = "gold",
    classification: str = "PUBLIC",
    metadata: dict[str, Any] | None = None,
) -> str:
    """Write a Shape model to ``path`` (a v1 capture is migrated first) and return its content
    id, the SHA-256 of ``shape.json``."""
    classification = _classification(classification)
    validate_structure(shape, allow_nonfinite=True)
    enforce_no_secrets(shape)
    metadata = metadata or {}
    validate_structure(metadata)
    enforce_no_secrets(metadata)
    model = to_model(shape, str(name))
    body = codec.dumps(model, sort_keys=True)
    content_id = hashlib.sha256(body).hexdigest()
    manifest = compat.stamp(
        "artifact",
        {
            "name": str(name),
            "shape_content_id": content_id,
            "fidelity": str(fidelity),
            "classification": classification,
            "metadata": metadata,
        },
    )
    write_artifact(path, manifest, {COMPONENT: body})
    return content_id


def read_model(path: Any, *, verify_key: bytes | None = None) -> ArtifactRead:
    """``(manifest, model)`` of a .shape file; a version 1 file is migrated (its manifest then
    says ``version`` 2, with ``migrated_from`` and ``source_content_id``). Any defect of
    the file is an ``ArtifactError``. With ``verify_key`` (a trusted Ed25519 public key) the file
    must carry a valid signature, else ``ArtifactSignatureError``. The tuple also has ``.signature``
    (``status`` ``verified``, ``unsigned`` or ``signed_not_verified``); without ``verify_key`` an
    unverified artifact also raises a notice (see ``read_artifact``)."""
    read = read_artifact(path, verify_key=verify_key)
    m, parts = read
    signature = read.signature
    if m.get("format") != FORMAT:
        raise ArtifactError("not a Shape artifact")
    version = compat.check_readable("artifact", m, error=ArtifactError)
    try:
        _classification(m.get("classification", "PUBLIC"))
    except ValueError as e:
        raise ArtifactError(str(e)) from e
    body = parts.get(COMPONENT)
    if body is None:
        raise ArtifactError(f"{COMPONENT} missing: not a Shape model artifact")
    expected = m.get("shape_content_id")
    actual = hashlib.sha256(body).hexdigest()
    if not isinstance(expected, str) or expected != actual:
        raise ArtifactError("Shape content identity mismatch")
    try:
        obj = codec.loads(body)
        validate_structure(obj, allow_nonfinite=True)
        if version < FORMAT_VERSION:
            m, obj, _ = MIGRATIONS.migrate(m, obj, FORMAT_VERSION)
            m["migrated_from"] = version
            m["source_content_id"] = actual
            m["shape_content_id"] = hashlib.sha256(codec.dumps(obj, sort_keys=True)).hexdigest()
        else:
            obj = to_model(obj)
    except (SecurityError, ArtifactError):
        raise
    except compat.UnsupportedVersionError as e:
        raise compat.error_class(ArtifactError)(
            f"invalid {COMPONENT}: {e}",
            kind=e.kind,
            found=e.found,
            supported=e.supported,
            min_shape_version=e.min_shape_version,
        ) from e
    except (ValueError, TypeError, KeyError, RecursionError, ModelError) as e:
        raise ArtifactError(f"invalid {COMPONENT}: {e}") from e
    return ArtifactRead(m, obj, signature)


def write_shape(
    path: Any,
    shape: dict[str, Any],
    *,
    name: str = "shape",
    fidelity: str = "gold",
    classification: str = "PUBLIC",
    metadata: dict[str, Any] | None = None,
) -> str:
    """:func:`write_model` under the name the v1 consumers know."""
    return write_model(
        path, shape, name=name, fidelity=fidelity, classification=classification, metadata=metadata
    )


def read_shape(path: Any, *, verify_key: bytes | None = None) -> ArtifactRead:
    """``(manifest, shape)`` as the v1 consumers read it: the v1 document of a migrated capture,
    or the v2 model of a file that has none."""
    read = read_model(path, verify_key=verify_key)
    m, model = read
    return ArtifactRead(m, legacy_view(model), read.signature)
