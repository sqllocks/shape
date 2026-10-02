"""The Fabric notebook template of the ``fabric_spark`` mode."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORKER_NAME = "shape_spark_worker.ipynb"


def worker_notebook() -> dict[str, Any]:
    """The worker notebook as a parsed ``.ipynb`` (placeholders unfilled)."""
    return dict(json.loads((Path(__file__).parent / WORKER_NAME).read_text(encoding="utf-8")))
