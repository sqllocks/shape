"""Print an image's uncompressed size in bytes: the sum of the files in its layers (PF-05).

    python3 scripts/image_size.py shape:ci          # -> 470982894

`docker image inspect --format '{{.Size}}'` is not a stable measure: with the classic image store
it is the uncompressed size, with the containerd image store (the default for new Docker Engine
installs) it is the compressed content size, about a third of it. The 500 MB gate is the
uncompressed size, so CI measures that from `docker save` whatever store the runner uses.
Standard library only; details go to stderr, the number alone to stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def uncompressed_layer_bytes(saved: Path) -> int:
    """Sum the regular-file sizes of every layer of a `docker save` archive."""
    total = 0
    with tarfile.open(saved) as archive:
        manifest = archive.extractfile("manifest.json")
        assert manifest is not None
        layers = json.load(manifest)[0]["Layers"]
        for name in layers:
            blob = archive.extractfile(name)
            assert blob is not None
            # "r|*" reads plain, gzip, bz2 or xz layer streams.
            with tarfile.open(fileobj=blob, mode="r|*") as layer:
                size = sum(member.size for member in layer if member.isreg())
            print(f"  {name}: {size / 1e6:.1f} MB", file=sys.stderr)
            total += size
    return total


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print("usage: image_size.py IMAGE", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory() as tmp:
        saved = Path(tmp) / "image.tar"
        subprocess.run(["docker", "image", "save", argv[0], "-o", str(saved)], check=True)
        total = uncompressed_layer_bytes(saved)
    print(f"{argv[0]}: {total / 1e6:.1f} MB uncompressed", file=sys.stderr)
    print(total)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
