"""The format declarations of the streaming package's persisted documents.

Every state the package writes (checkpoints, window snapshots, the deduplicator's and the keyed
sketches' windows, the emit checkpoint and the live fidelity report) declares ``format`` and an
integer ``version``, as ``docs/specs/STATE_AND_COMPATIBILITY.md`` asks. ``format`` keeps the
string the first release wrote (it ends in ``-v1``, which is only part of the name); a document
that declares no version is version 1. A reader calls :func:`check` first, so a document of a
newer version is refused as newer (with the release that reads it), not as "not a checkpoint".

The live alert lines are the exception: their key set is the documented line format
(``docs/EMIT.md``) and is pinned by a test, so they declare ``format`` only.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from shape import compat

_FIRST = "0.9.0"


def _kind(name: str, label: str, format: str) -> compat.Kind:
    return compat.Kind(
        name=name,
        label=label,
        format=format,
        current=1,
        first_release={1: _FIRST},
        implicit_version=1,
    )


STREAM_CHECKPOINT = _kind(
    "stream-checkpoint", "stream-profile checkpoint", "shape-stream-checkpoint-v1"
)
WINDOW_SNAPSHOT = _kind(
    "stream-window-snapshot", "stream window snapshot", "shape-stream-window-v1"
)
DEDUPE_SNAPSHOT = _kind("dedupe-snapshot", "deduplicator snapshot", "shape-dedupe-v1")
SKETCHES_SNAPSHOT = _kind(
    "keyed-sketches-snapshot", "keyed-sketches snapshot", "shape-keyed-sketches-v1"
)
KEYED_STATE = _kind("keyed-state-snapshot", "keyed-state snapshot", "shape-keyed-state-v1")
PARTITIONED_KEYED_STATE = _kind(
    "partitioned-keyed-state-snapshot",
    "partitioned keyed-state snapshot",
    "shape-keyed-state-partitioned-v1",
)
EMIT_CHECKPOINT = _kind("emit-checkpoint", "emit checkpoint", "shape-emit-v1")
LIVE_REPORT = _kind("live-report", "live fidelity report", "shape-live-fidelity-v1")


def stamp(kind: compat.Kind, doc: Mapping[str, Any]) -> dict[str, Any]:
    """``doc`` with ``format``, ``version``, ``shape_version`` and ``min_shape_version`` first."""
    return compat.stamp(kind, doc)


def check(
    kind: compat.Kind,
    doc: Mapping[str, Any],
    source: object = "",
    *,
    error: type[Exception] | None = None,
) -> int:
    """The version of ``doc``, which must be this kind (its ``format`` is the kind's, or absent in
    a document from before formats were declared) and not newer than this release reads."""
    compat.check_format(kind, doc, error=error)
    return compat.check_readable(kind, doc, source, error=error)
