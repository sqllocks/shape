"""Maps a Shape profile onto the pinned baseline's ``TableProfile`` / dataset JSON.

Internal to the parity harness: nothing in ``src/`` knows this format. The profile's
``to_dict()`` uses the baseline's field names, so the mapping is a load and a dump.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))


def profile_json(shape_file: Path | str) -> dict[str, Any]:
    """The baseline-shaped JSON document for a ``.shape`` profile file."""
    import shape

    doc: dict[str, Any] = shape.load(str(shape_file)).to_dict()
    return doc


def write_profile_json(shape_file: Path | str, out: Path | str) -> None:
    Path(out).write_text(json.dumps(profile_json(shape_file), indent=2), encoding="utf-8")
