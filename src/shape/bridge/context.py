"""What a command handler gets besides its arguments (P6-11)."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.bridge.errors import writing
from shape.bridge.jobs import Jobs
from shape.bridge.protocol import DEFAULT_MAX_INLINE_BYTES, warning


def _noop(_info: dict[str, Any]) -> None:
    return None


@dataclass
class Context:
    """Per request. ``options`` are the envelope options; ``warnings`` collects what the response
    reports beside its result; inside a job, ``cancel`` is set when the job is asked to stop and
    ``progress`` records how far it is."""

    jobs: Jobs
    options: dict[str, Any] = field(default_factory=dict)
    warnings: list[dict[str, str]] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    progress: Callable[[dict[str, Any]], None] = _noop
    in_job: bool = False

    @property
    def jobs_dir(self) -> Path:
        return self.jobs.store.root

    @property
    def include_raw(self) -> bool:
        return bool(self.options.get("include_raw_values", False))

    @property
    def max_inline(self) -> int:
        return int(self.options.get("max_inline_bytes", DEFAULT_MAX_INLINE_BYTES))

    def warn(self, code: str, message: str) -> None:
        self.warnings.append(warning(code, message))

    @property
    def results_dir(self) -> Path:
        return self.jobs_dir / "bridge" / "results"

    def spill(self, name: str, value: Any) -> Any:
        """``value`` itself when it is small enough to return inline, else a file reference:
        ``{"spilled": true, "content_id": "sha256:...", "path": ..., "bytes": N}``. The file is
        the value as JSON, named by its content id."""
        text = json.dumps(value, sort_keys=True, default=str, allow_nan=False)
        size = len(text.encode("utf-8"))
        if size <= self.max_inline:
            return value
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        target = self.results_dir / f"{digest}.json"
        with writing():
            self.results_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not target.exists():
                fd, tmp = tempfile.mkstemp(dir=self.results_dir, prefix=".part-", suffix=".tmp")
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(text)
                    os.chmod(tmp, 0o600)
                    os.replace(tmp, target)
                except BaseException:
                    Path(tmp).unlink(missing_ok=True)
                    raise
        self.warn(
            "result_in_file",
            f"{name} is {size} bytes: written to a file (content id {digest[:12]}...)",
        )
        return {
            "spilled": True,
            "content_id": digest,
            "path": str(target),
            "bytes": size,
        }
