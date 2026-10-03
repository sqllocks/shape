"""Record the expected canonical form of every file of a generation.

    python tests/timecapsule/capsule_bless.py tests/timecapsule/corpus/<generation>

Run it once, by the release that wrote the generation (or the one that introduces it). The
expected files are then frozen with the corpus: a later release that reads a file differently has
broken the read-old promise, and the fix is in the reader, never in the expected file.
"""

from __future__ import annotations

import json
import sys
import warnings
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from capsule_loaders import canonical_of  # noqa: E402


def bless(corpus: Path) -> None:
    index = json.loads((corpus / "index.json").read_text())
    out = HERE / "expected" / corpus.name
    out.mkdir(parents=True, exist_ok=True)
    warnings.simplefilter("ignore")
    for entry in index["files"]:
        target = out / f"{entry['id']}.json"
        if target.exists():
            raise SystemExit(f"{target} exists: expected files are written once")
        target.write_text(canonical_of(corpus, entry) + "\n", encoding="utf-8")
        print("blessed", entry["id"])


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    bless(Path(sys.argv[1]).resolve())
