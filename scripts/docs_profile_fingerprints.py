"""Validate documentation profile identities without comparing CPU-specific fit roundoff.

The baseline is generated from the unchanged runnable transcripts by
``python scripts/gen_docs_profile_fingerprints.py``. Only the explicitly listed
CPU-variable distribution-fit parameters change presentation precision. Every
other field, component, and mapping order remains in the fingerprint.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

BASELINE = "tests/docs/profile_fingerprints.json"
DATASET_FIT_FIELDS = {
    ("product", "cost"): {"s": 12, "loc": 12},
    ("product", "unit_price"): {"loc": 12},
}
TABLE_FIT_FIELDS = {
    "customer_id": {"s": 12, "loc": 12, "scale": 12},
    "order_total": {"s": 9, "loc": 9, "scale": 9},
}


def _fit_fields(data: Any) -> Any:
    data = copy.deepcopy(data)

    def present(column: dict[str, Any], fields: dict[str, int]) -> None:
        params = column.get("distribution_params")
        if isinstance(params, dict):
            for key, digits in fields.items():
                if isinstance(params.get(key), float):
                    params[key] = float(f"{params[key]:.{digits}g}")

    for (table_name, column_name), fields in DATASET_FIT_FIELDS.items():
        column = data.get("tables", {}).get(table_name, {}).get("columns", {}).get(column_name)
        if isinstance(column, dict):
            present(column, fields)
    if data.get("name") == "orders":
        for column_name, fields in TABLE_FIT_FIELDS.items():
            column = data.get("columns", {}).get(column_name)
            if isinstance(column, dict):
                present(column, fields)
    return data


def identities(work: Path) -> dict[str, str]:
    """Return verified profile/body and registry identities of local artifacts."""
    from shape.artifact import codec
    from shape.artifact.io import ArtifactError, read_artifact

    candidates = list(work.rglob("*.shape"))
    candidates += [path for path in work.rglob("objects/*") if len(path.name) == 64]
    nodes = []
    for path in candidates:
        if not path.is_file() or path.is_symlink():
            continue
        try:
            manifest, parts = read_artifact(str(path), notice=False)
        except (ArtifactError, ValueError, OSError):
            continue
        if manifest.get("kind") != "profile" or "profile.json" not in parts:
            continue
        cid = hashlib.sha256(parts["profile.json"]).hexdigest()
        if cid != manifest.get("shape_content_id"):
            raise ValueError(f"documentation artifact identity mismatch: {path}")
        data = _fit_fields(codec.loads(parts["profile.json"]))
        nodes.append((path, cid, manifest, parts, data))
    known: dict[str, str] = {}
    for _ in range(len(nodes) + 1):
        result: dict[str, str] = {}
        for path, cid, manifest, parts, data in nodes:
            document = copy.deepcopy(data)
            for source in document.get("merge", {}).get("inputs", []):
                old = source.get("shape_content_id")
                if old in known:
                    source["shape_content_id"] = known[old]
            body_fingerprint = hashlib.sha256(codec.dumps(document, sort_keys=False)).hexdigest()
            result[cid] = "profile:" + body_fingerprint
            envelope = copy.deepcopy(manifest)
            envelope["shape_content_id"] = body_fingerprint
            envelope["content_hashes"]["profile.json"] = body_fingerprint
            component_hashes = {
                name: body_fingerprint
                if name == "profile.json"
                else hashlib.sha256(value).hexdigest()
                for name, value in parts.items()
            }
            raw_fingerprint = hashlib.sha256(
                json.dumps([envelope, component_hashes], ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            result[hashlib.sha256(path.read_bytes()).hexdigest()] = "registry:" + raw_fingerprint
        if result == known:
            return result
        known = result
    return known


def comparable_ids(text: str, work: Path, root: Path, *, recorded: bool) -> str:
    """Map only baseline identities whose complete local artifacts match the baseline."""
    import re

    if re.search(r"\b[0-9a-f]{64}\b", text) is None:
        return text
    path = root / BASELINE
    if not path.exists():
        return text
    baseline = json.loads(path.read_text(encoding="utf-8"))["identities"]
    mapping = baseline if recorded else identities(work)
    approved = set(baseline.values())

    def replace(match: re.Match[str]) -> str:
        fingerprint = mapping.get(match.group(0))
        return "<" + fingerprint + ">" if fingerprint in approved else match.group(0)

    return re.sub(r"\b[0-9a-f]{64}\b", replace, text)
