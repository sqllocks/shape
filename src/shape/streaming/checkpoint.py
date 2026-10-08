"""Atomic local checkpoint persistence.

A checkpoint file is replaced as a whole (written to a temporary file in the same directory,
flushed to disk, then renamed over the old one), so a reader sees the old checkpoint or the new
one, never half of either, even if the writer is killed mid-write.
"""

from __future__ import annotations

import json
import os
import tempfile
import zlib
from pathlib import Path
from typing import Any

from .core import StreamCheckpoint

MAX_STATE_BYTES = 256 << 20
"""The most a compressed state field may expand to when its size is not declared (a profile
window's state): a checkpoint is a file another process or user may have written."""


def inflate(raw: bytes, limit: int = MAX_STATE_BYTES) -> bytes:
    """``raw`` (zlib data) decompressed, which must be at most ``limit`` bytes and a whole stream:
    a damaged or hostile field fails with a ``ValueError`` (a corrupt checkpoint) instead of
    filling memory."""
    stream = zlib.decompressobj()
    try:
        out = stream.decompress(raw, limit + 1)
    except zlib.error as exc:
        raise ValueError(f"corrupt checkpoint: a state field does not decode ({exc})") from None
    if len(out) > limit or stream.unconsumed_tail:
        raise ValueError(
            "corrupt checkpoint: a compressed state field expands past its size limit of "
            f"{limit:,} bytes"
        )
    if not stream.eof:
        raise ValueError("corrupt checkpoint: a compressed state field is truncated")
    return out


class CheckpointError(ValueError):
    """A checkpoint file that cannot be read, or does not fit the stream resuming from it."""


class FileCheckpointStore:
    """A checkpoint in one JSON file.

    ``save``/``load`` keep a ``StreamCheckpoint`` (a sequence number and a token);
    ``save_document``/``load_document`` keep a whole JSON document, which is what the stream
    consumer uses (source offsets, deduplication positions and the profiler's window state).
    """

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)

    def _write(self, document: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                json.dump(document, f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def _read(self) -> Any:
        if not self.path.exists():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CheckpointError(f"cannot read the checkpoint {self.path}: {exc}") from exc

    def save(self, checkpoint: StreamCheckpoint) -> None:
        self._write({"sequence": checkpoint.sequence, "token": checkpoint.token})

    def load(self) -> StreamCheckpoint | None:
        o = self._read()
        if o is None:
            return None
        try:
            return StreamCheckpoint(int(o["sequence"]), o.get("token"))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CheckpointError(f"the checkpoint {self.path} is not a stream checkpoint") from exc

    def save_document(self, document: dict[str, Any]) -> None:
        self._write(document)

    def load_document(self) -> dict[str, Any] | None:
        o = self._read()
        if o is not None and not isinstance(o, dict):
            raise CheckpointError(f"the checkpoint {self.path} is not a JSON object")
        return o
