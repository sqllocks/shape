"""A depth check to run before a file reaches pyarrow's JSON reader (P7-04).

pyarrow 25's reader recurses without a limit: a 200 KB line of ``{"a":[[[[...`` crashes the
whole process with a segmentation fault, which Python cannot catch. The check counts bracket
depth per line with numpy, ignoring nothing (a bracket inside a string counts, which can only
reject a pathological line, never accept a deep one).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

MAX_JSON_DEPTH = 128
_CHUNK = 8 * 1024 * 1024


def _max_line_depth(buf: np.ndarray, limit: int) -> int:
    """The deepest line of ``buf`` (a value above ``limit`` means "too deep", not an exact depth).

    A line cannot nest deeper than it has opening brackets, so a vectorised count per line picks
    the few lines worth an exact bracket walk."""
    import numpy as np

    if not buf.size:
        return 0
    opens = (buf == 0x5B) | (buf == 0x7B)
    starts = np.concatenate(([0], np.flatnonzero(buf == 0x0A) + 1))
    starts = starts[starts < buf.size]
    counts = np.add.reduceat(opens, starts, dtype=np.int32)
    deepest = 0
    for k in np.flatnonzero(counts > limit):
        lo = int(starts[k])
        hi = int(starts[k + 1]) if k + 1 < len(starts) else buf.size
        line = buf[lo:hi]
        walk = np.cumsum(
            ((line == 0x5B) | (line == 0x7B)).astype(np.int32)
            - ((line == 0x5D) | (line == 0x7D)).astype(np.int32)
        )
        deepest = max(deepest, int(walk.max()))
    return deepest


def check_json_depth(data: bytes, limit: int = MAX_JSON_DEPTH) -> None:
    """``ValueError`` when any line of ``data`` nests deeper than ``limit``."""
    import numpy as np

    start = 0
    while start < len(data):
        end = min(len(data), start + _CHUNK)
        if end < len(data):  # cut at a line end so a line is never split between chunks
            cut = data.rfind(b"\n", start, end)
            end = cut + 1 if cut >= start else len(data)
        if (
            _max_line_depth(
                np.frombuffer(data, dtype=np.uint8, count=end - start, offset=start), limit
            )
            > limit
        ):
            raise ValueError(f"JSON is nested deeper than {limit} levels")
        start = end


def check_json_file(path: str | Path, limit: int = MAX_JSON_DEPTH) -> None:
    """:func:`check_json_depth` over a file, read in line-aligned chunks."""
    with open(path, "rb") as fh:
        carry = b""
        while True:
            block = fh.read(_CHUNK)
            data = carry + block
            if not block:
                check_json_depth(data, limit)
                return
            cut = data.rfind(b"\n")
            if cut < 0:  # one huge line: keep reading until it ends
                carry = data
                continue
            check_json_depth(data[: cut + 1], limit)
            carry = data[cut + 1 :]
