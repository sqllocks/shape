"""String distances and similarities for entity resolution.

``levenshtein`` is the scalar reference; ``levenshtein_pairs`` computes the same integers for many
pairs at once with numpy (a dynamic-programming table advanced for every pair in one array
operation). Both use code points, so they agree exactly. Every similarity is in 0 to 1, symmetric,
and 1.0 for two empty strings. Nothing here draws a random number.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable, Sequence
from typing import cast

import numpy as np
import numpy.typing as npt

_APOSTROPHES = "'’‘`"
_NOT_WORD = re.compile(r"[^0-9a-z]+")

STRING_MEASURES = ("levenshtein", "jaro_winkler", "ngram", "tokens", "text")


def normalize_text(value: object) -> str:
    """Lower case, accents folded, apostrophes dropped, other punctuation as one space, trimmed."""
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).lower()
    for ch in _APOSTROPHES:
        text = text.replace(ch, "")
    return _NOT_WORD.sub(" ", text).strip()


def levenshtein(a: str, b: str) -> int:
    """The edit distance (insert, delete, substitute; each costs 1)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _codes(strings: Sequence[str], width: int) -> npt.NDArray[np.uint32]:
    out = np.zeros((len(strings), max(width, 1)), dtype=np.uint32)
    for k, s in enumerate(strings):
        if s:
            out[k, : len(s)] = np.frombuffer(s.encode("utf-32-le"), dtype="<u4")
    return out


def levenshtein_pairs(
    a: Sequence[str], b: Sequence[str], *, chunk: int = 20_000
) -> npt.NDArray[np.int64]:
    """``levenshtein(a[k], b[k])`` for every k, as int64; equal to the scalar form exactly."""
    if len(a) != len(b):
        raise ValueError("levenshtein_pairs needs two sequences of the same length")
    n = len(a)
    out = np.zeros(n, dtype=np.int64)
    step = max(int(chunk), 1)
    for start in range(0, n, step):
        out[start : start + step] = _levenshtein_chunk(
            a[start : start + step], b[start : start + step]
        )
    return out


def _levenshtein_chunk(a: Sequence[str], b: Sequence[str]) -> npt.NDArray[np.int64]:
    p = len(a)
    la = np.fromiter((len(s) for s in a), dtype=np.int64, count=p)
    lb = np.fromiter((len(s) for s in b), dtype=np.int64, count=p)
    ma, mb = (
        int(cast(Callable[..., np.int64], la.max)(initial=0)),
        int(cast(Callable[..., np.int64], lb.max)(initial=0)),
    )
    codes_a, codes_b = _codes(a, ma), _codes(b, mb)
    result = np.where(la == 0, lb, 0).astype(np.int64)
    prev = np.tile(np.arange(mb + 1, dtype=np.int64), (p, 1))
    rows = np.arange(p)
    for i in range(1, ma + 1):
        cur = np.empty_like(prev)
        cur[:, 0] = i
        ai = codes_a[:, i - 1]
        for j in range(1, mb + 1):
            cost = (ai != codes_b[:, j - 1]).astype(np.int64)
            cur[:, j] = np.minimum(
                np.minimum(prev[:, j] + 1, cur[:, j - 1] + 1), prev[:, j - 1] + cost
            )
        done = la == i
        if done.any():
            result[done] = cur[rows[done], lb[done]]
        prev = cur
    return result


def jaro(a: str, b: str) -> float:
    """The Jaro similarity."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    window = max(max(la, lb) // 2 - 1, 0)
    flags_a, flags_b = [False] * la, [False] * lb
    matches = 0
    for i, ca in enumerate(a):
        for j in range(max(0, i - window), min(lb, i + window + 1)):
            if not flags_b[j] and b[j] == ca:
                flags_a[i] = flags_b[j] = True
                matches += 1
                break
    if not matches:
        return 0.0
    k = transpositions = 0
    for i in range(la):
        if flags_a[i]:
            while not flags_b[k]:
                k += 1
            if a[i] != b[k]:
                transpositions += 1
            k += 1
    m = float(matches)
    return (m / la + m / lb + (m - transpositions / 2) / m) / 3.0


def jaro_winkler(a: str, b: str, *, scale: float = 0.1, boost_from: float = 0.7) -> float:
    """Jaro with the Winkler bonus for a shared prefix of up to four characters."""
    base = jaro(a, b)
    if base <= boost_from:
        return base
    prefix = 0
    for ca, cb in zip(a[:4], b[:4], strict=False):
        if ca != cb:
            break
        prefix += 1
    return base + prefix * scale * (1.0 - base)


def _grams(s: str, q: int) -> set[str]:
    if not s:
        return set()
    if len(s) < q:
        return {s}
    return {s[i : i + q] for i in range(len(s) - q + 1)}


def ngram_jaccard(a: str, b: str, q: int = 3) -> float:
    """Jaccard similarity of the sets of character q-grams."""
    ga, gb = _grams(a, q), _grams(b, q)
    if not ga and not gb:
        return 1.0
    return len(ga & gb) / len(ga | gb)


def _lev_similarity(a: str, b: str) -> float:
    longest = max(len(a), len(b))
    return 1.0 if longest == 0 else 1.0 - levenshtein(a, b) / longest


def token_sort_similarity(a: str, b: str) -> float:
    """Levenshtein similarity after sorting the words of each side (word order is ignored)."""
    ta = " ".join(sorted(normalize_text(a).split()))
    tb = " ".join(sorted(normalize_text(b).split()))
    return _lev_similarity(ta, tb)


_SOUNDEX = {
    **dict.fromkeys("BFPV", "1"),
    **dict.fromkeys("CGJKQSXZ", "2"),
    **dict.fromkeys("DT", "3"),
    "L": "4",
    **dict.fromkeys("MN", "5"),
    "R": "6",
}


def soundex(word: str) -> str:
    """American Soundex of one word (``""`` when it has no letters)."""
    text = unicodedata.normalize("NFKD", word)
    letters = [ch for ch in text.upper() if "A" <= ch <= "Z"]
    if not letters:
        return ""
    out = letters[0]
    prev = _SOUNDEX.get(letters[0], "")
    for ch in letters[1:]:
        if ch in "HW":
            continue
        code = _SOUNDEX.get(ch, "")
        if code and code != prev:
            out += code
        prev = code
    return (out + "000")[:4]


def phonetic_key(value: object) -> str:
    """The sorted Soundex codes of the words of a value, joined by a space."""
    codes = sorted(c for c in (soundex(t) for t in normalize_text(value).split()) if c)
    return " ".join(codes)


def similarity_pairs(kind: str, a: Sequence[str], b: Sequence[str]) -> npt.NDArray[np.float64]:
    """The similarity (0 to 1) of ``a[k]`` and ``b[k]`` under ``kind``, after normalizing both.

    ``levenshtein``: 1 - distance / longer length; ``jaro_winkler``; ``ngram``: trigram Jaccard;
    ``tokens``: word order ignored; ``text``: the larger of ``jaro_winkler`` and ``tokens``.
    """
    if kind not in STRING_MEASURES:
        raise ValueError(
            f"unknown string measure {kind!r}; choose one of {', '.join(STRING_MEASURES)}"
        )
    if len(a) != len(b):
        raise ValueError("similarity_pairs needs two sequences of the same length")
    na = [normalize_text(x) for x in a]
    nb = [normalize_text(x) for x in b]
    if kind == "levenshtein":
        dist = levenshtein_pairs(na, nb)
        longest = np.fromiter(
            (max(len(x), len(y)) for x, y in zip(na, nb, strict=True)),
            dtype=np.int64,
            count=len(na),
        )
        return np.where(longest == 0, 1.0, 1.0 - dist / np.maximum(longest, 1)).astype(np.float64)
    if kind == "jaro_winkler":
        return np.array([jaro_winkler(x, y) for x, y in zip(na, nb, strict=True)], dtype=np.float64)
    if kind == "ngram":
        return np.array(
            [ngram_jaccard(x, y) for x, y in zip(na, nb, strict=True)], dtype=np.float64
        )
    if kind == "tokens":
        return np.array(
            [token_sort_similarity(x, y) for x, y in zip(na, nb, strict=True)], dtype=np.float64
        )
    return np.array(
        [max(jaro_winkler(x, y), token_sort_similarity(x, y)) for x, y in zip(na, nb, strict=True)],
        dtype=np.float64,
    )
