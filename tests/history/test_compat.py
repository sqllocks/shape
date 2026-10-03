"""W3-03: the persisted formats of the history tools declare ``format`` and an integer ``version``,
and a frozen version-1 document keeps its shape (a later version may add keys, never drop one)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from history_helpers import layered, total_step, value_history

from shape.history import bisect, bisect_layers
from shape.registry.local import LocalRegistry

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "history"


def keys(doc: Any, prefix: str = "") -> set[str]:
    """Every key path of the objects in ``doc`` (lists are walked through their first item)."""
    out: set[str] = set()
    if isinstance(doc, dict):
        for k, v in doc.items():
            out.add(f"{prefix}{k}")
            out |= keys(v, f"{prefix}{k}.")
    elif isinstance(doc, list) and doc:
        out |= keys(doc[0], prefix)
    return out


def frozen(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((FIXTURES / "v1" / name).read_text())
    return doc


def test_bisect_result_keeps_its_version_1_keys(tmp_path: Path):
    h = value_history(tmp_path, 6, {"v": 3})
    log = LocalRegistry(h.registry).log("feed")
    now = bisect(h.registry, "feed", good=log[0]["content_id"], bad=log[5]["content_id"]).to_dict()
    old = frozen("bisect.json")
    assert (old["format"], old["version"]) == ("shape-bisect", 1)
    assert (now["format"], now["version"]) == ("shape-bisect", 1)
    assert keys(old) <= keys(now)


def test_layers_result_keeps_its_version_1_keys(tmp_path: Path):
    step = [total_step(2)]
    project = layered(tmp_path, {"raw": {}, "clean": {"events": step}, "pub": {"events": step}})
    now = bisect_layers(
        ["raw", "clean", "pub"], good_date="2026-03-01", bad_date="2026-03-03", project=project
    ).to_dict()
    old = frozen("bisect-layers.json")
    assert (old["format"], old["version"]) == ("shape-bisect-layers", 1)
    assert (now["format"], now["version"]) == ("shape-bisect-layers", 1)
    assert keys(old) <= keys(now)


def test_frozen_documents_are_what_the_tools_wrote():
    old = frozen("bisect.json")
    assert old["found"] is True and old["first_bad"]["business_date"] == "2026-03-04"
    layers = frozen("bisect-layers.json")
    assert layers["first_layer"] == "clean" and layers["persists"] == ["pub"]
    for name in ("bisect.json", "bisect-layers.json", "timelapse.json"):
        doc = frozen(name)
        assert isinstance(doc["version"], int) and not isinstance(doc["version"], bool)
        assert isinstance(doc["format"], str) and doc["format"].startswith("shape-")
