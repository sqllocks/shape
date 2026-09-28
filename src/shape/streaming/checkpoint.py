"""Atomic local checkpoint persistence."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .core import StreamCheckpoint


class FileCheckpointStore:
    def __init__(self, path):
        self.path = Path(path)

    def save(self, checkpoint: StreamCheckpoint):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.path.name + ".", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    {"sequence": checkpoint.sequence, "token": checkpoint.token},
                    f,
                    separators=(",", ":"),
                )
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def load(self):
        if not self.path.exists():
            return None
        o = json.loads(self.path.read_text(encoding="utf-8"))
        return StreamCheckpoint(int(o["sequence"]), o.get("token"))
