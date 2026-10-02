"""Download from an official source, verify a pinned checksum, cache the file.

Nothing here runs at import time or at lookup time: only ``shape healthcare-codes fetch`` calls
it. Downloads are cached in ``<data dir>/downloads/`` so a rebuild does not hit the network.
A source whose publisher gives no checksum (the FDA NDC directory is rebuilt daily) is recorded
by the SHA-256 of the bytes that were used, in the asset manifest.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from pathlib import Path

from shape_healthcare_codes.store import user_dir

ALLOWED_SCHEMES = ("https",)
"""Only https downloads (tests widen this to ``file``)."""
_UA = "shape-healthcare-codes/0.9 (+https://github.com/sqllocks/shape)"


class ChecksumMismatch(RuntimeError):
    """A downloaded file does not match its pinned checksum."""


def digest(path: Path, algo: str = "sha256") -> str:
    h = hashlib.new(algo)
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(path: Path, pin: str | None) -> str:
    """Check ``path`` against ``"algo:hex"`` (when given); return its SHA-256."""
    if pin:
        algo, _, want = pin.partition(":")
        got = digest(path, algo)
        if got != want:
            raise ChecksumMismatch(
                f"{path.name}: {algo} is {got}, pinned {want}. The publisher changed the file; "
                f"re-read the licence and release notes, then update the pin."
            )
    return digest(path, "sha256")


def download(
    url: str, dest_dir: Path | None = None, *, pin: str | None = None, name: str | None = None
) -> Path:
    """Fetch ``url`` into the download cache (reusing a cached copy that passes ``pin``)."""
    scheme = urllib.parse.urlparse(url).scheme
    if scheme not in ALLOWED_SCHEMES:
        raise ValueError(f"{url!r}: only {', '.join(ALLOWED_SCHEMES)} downloads are allowed")
    d = dest_dir or user_dir() / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    # The whole path names the cache file: two sources can end in the same file name
    # (CMS has ".../updated-01/11/2023.zip" for both ICD-10-CM and ICD-10-PCS).
    path_name = urllib.parse.unquote(urllib.parse.urlparse(url).path).strip("/")
    target = d / (name or re.sub(r"[^A-Za-z0-9._-]+", "_", path_name))
    if target.is_file():
        try:
            verify(target, pin)
            return target
        except ChecksumMismatch:
            target.unlink()
    last: Exception | None = None
    for attempt in range(4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            tmp = target.with_suffix(target.suffix + ".part")
            with urllib.request.urlopen(req, timeout=120) as r, tmp.open("wb") as out:  # nosec B310
                shutil.copyfileobj(r, out)
            verify(tmp, pin)
            tmp.replace(target)
            return target
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"could not download {url}: {last}")


def sources_manifest(files: Mapping[str, Path]) -> dict[str, str]:
    """``{file name: sha256}`` for the manifest."""
    return {name: digest(p) for name, p in sorted(files.items())}
