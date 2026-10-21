"""Write the additive W9-05 capsule once; existing generations are never rewritten."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def generate(directory: Path, expected: Path) -> None:
    import pyarrow as pa
    from capsule_loaders import canonical_of

    import shape

    if directory.exists() or expected.exists():
        raise ValueError("capsule generation already exists")
    (directory / "profile").mkdir(parents=True)
    expected.mkdir(parents=True)
    profile = shape.profile(
        pa.table({"items": [[{"amount": 3.0}], [], None] * 10}), name="nested-children", joint=False
    )
    entries = []
    for capture in ("safe", "full"):
        relative = f"profile/children-{capture}.shape"
        path = directory / relative
        shape.save(profile, path, capture=capture)
        entry = {
            "id": f"profile-artifact-v2-children-{capture}",
            "kind": "profile-artifact",
            "format_version": 2,
            "path": relative,
            "produced_by": f"shape.save(capture='{capture}')",
            "writer_shape_version": shape.__version__,
            "authored": False,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        entries.append(entry)
        (expected / (entry["id"] + ".json")).write_text(canonical_of(directory, entry))
    (directory / "index.json").write_text(json.dumps({"files": entries}, indent=2) + "\n")


if __name__ == "__main__":
    root = Path(__file__).parent
    generate(root / "corpus" / "nested-children", root / "expected" / "nested-children")
