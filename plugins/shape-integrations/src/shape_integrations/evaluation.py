"""The ``shape-evaluation`` report: what ``shape evaluate`` writes (format, version 1).

::

    {"format": "shape-evaluation", "version": 1, "tool": "sdmetrics" | "anonymeter",
     "tool_version": "...", "results": {...}}

``results`` is the tool's own outcome, in the shape documented in docs/plugins/integrations.md.
A reader of version 1 refuses a report of a higher version and a report of another format.
The report holds scores, counts and column names, never row values.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

EVALUATION_FORMAT = "shape-evaluation"
EVALUATION_VERSION = 1
TOOLS = ("sdmetrics", "anonymeter")


class EvaluationReportError(ValueError):
    """A file is not an evaluation report this version can read."""


class EvaluationVersionError(EvaluationReportError):
    """The report was written by a newer Shape."""


def clean(value: Any) -> Any:
    """``value`` as JSON-safe data: NaN and infinity become ``None``, containers are copied."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, "item") and callable(value.item):  # a NumPy scalar
        return clean(value.item())
    return value


def make_report(tool: str, tool_version: str, results: dict[str, Any]) -> dict[str, Any]:
    if tool not in TOOLS:
        raise EvaluationReportError(f"unknown tool {tool!r}; known: {', '.join(TOOLS)}")
    return {
        "format": EVALUATION_FORMAT,
        "version": EVALUATION_VERSION,
        "tool": tool,
        "tool_version": tool_version,
        "results": clean(results),
    }


def to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"


def read_report(path: str | Path) -> dict[str, Any]:
    """Read and check a report; raises :class:`EvaluationReportError`."""
    p = Path(path)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EvaluationReportError(f"{p}: cannot read as JSON: {exc}") from None
    if not isinstance(raw, dict) or raw.get("format") != EVALUATION_FORMAT:
        raise EvaluationReportError(f"{p} is not a {EVALUATION_FORMAT} report")
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise EvaluationReportError(f"{p}: 'version' must be an integer, got {version!r}")
    if version > EVALUATION_VERSION:
        raise EvaluationVersionError(
            f"{p} is {EVALUATION_FORMAT} version {version}, written by a newer Shape; this Shape "
            f"reads versions up to {EVALUATION_VERSION}. Upgrade Shape to read it"
        )
    if raw.get("tool") not in TOOLS:
        raise EvaluationReportError(f"{p}: unknown tool {raw.get('tool')!r}")
    if not isinstance(raw.get("tool_version"), str) or not isinstance(raw.get("results"), dict):
        raise EvaluationReportError(f"{p}: 'tool_version' and 'results' are required")
    return raw
