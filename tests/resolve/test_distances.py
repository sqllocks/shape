"""W3-09 (#72): string distances. The numpy batch form agrees exactly with the scalar reference."""

from __future__ import annotations

import numpy as np
import pytest

from shape.resolve.distances import (
    jaro,
    jaro_winkler,
    levenshtein,
    levenshtein_pairs,
    ngram_jaccard,
    normalize_text,
    similarity_pairs,
    soundex,
    token_sort_similarity,
)

CASES = [
    ("kitten", "sitting", 3),
    ("", "", 0),
    ("", "abc", 3),
    ("abc", "", 3),
    ("flaw", "lawn", 2),
    ("same", "same", 0),
    ("ab", "ba", 2),
    ("müller", "muller", 1),
]


@pytest.mark.parametrize(("a", "b", "d"), CASES)
def test_levenshtein_known_values(a: str, b: str, d: int) -> None:
    assert levenshtein(a, b) == d


def test_batch_levenshtein_equals_scalar_exactly() -> None:
    rng = np.random.default_rng(3)
    alphabet = list("abcde fgh")
    words = ["".join(rng.choice(alphabet, size=int(rng.integers(0, 14)))) for _ in range(300)]
    a = [words[i] for i in rng.integers(0, 300, 500)]
    b = [words[i] for i in rng.integers(0, 300, 500)]
    got = levenshtein_pairs(a, b)
    assert got.dtype == np.int64
    assert got.tolist() == [levenshtein(x, y) for x, y in zip(a, b, strict=True)]


def test_batch_levenshtein_handles_empty_input_and_chunking() -> None:
    assert levenshtein_pairs([], []).tolist() == []
    a = ["abc", "x"] * 5
    assert levenshtein_pairs(a, ["abd", "xyz"] * 5, chunk=3).tolist() == [1, 2] * 5
    with pytest.raises(ValueError, match="same length"):
        levenshtein_pairs(["a"], [])


def test_jaro_and_winkler_known_values() -> None:
    assert jaro("martha", "marhta") == pytest.approx(0.944444, abs=1e-6)
    assert jaro_winkler("martha", "marhta") == pytest.approx(0.961111, abs=1e-6)
    assert jaro("", "") == 1.0 and jaro("a", "") == 0.0
    assert jaro_winkler("dixon", "dicksonx") == pytest.approx(0.813333, abs=1e-6)


def test_ngram_and_token_measures() -> None:
    assert ngram_jaccard("night", "nacht", 2) == pytest.approx(1 / 7)
    assert ngram_jaccard("same", "same") == 1.0
    assert ngram_jaccard("", "") == 1.0
    assert token_sort_similarity("john smith", "smith john") == 1.0
    assert token_sort_similarity("john smith", "jon smith") < 1.0


def test_soundex_codes() -> None:
    assert soundex("Robert") == soundex("Rupert") == "R163"
    assert soundex("Tymczak") == "T522"
    assert soundex("Pfister") == "P236"
    assert soundex("Ashcraft") == "A261"
    assert soundex("") == "" and soundex("123") == ""


def test_normalize_text_folds_case_accents_punctuation_and_space() -> None:
    assert normalize_text("  José  O'Neil-Smith, Jr. ") == "jose oneil smith jr"
    assert normalize_text(None) == ""


def test_similarity_pairs_by_kind_is_symmetric_and_bounded() -> None:
    a = ["john smith", "ann", "", "acme corp"]
    b = ["jon smith", "anne", "", "acme corporation"]
    for kind in ("levenshtein", "jaro_winkler", "ngram", "tokens", "text"):
        s = similarity_pairs(kind, a, b)
        assert s.shape == (4,) and np.all((0.0 <= s) & (s <= 1.0))
        assert np.allclose(s, similarity_pairs(kind, b, a))
        assert s[2] == 1.0  # two empty strings are equal
    with pytest.raises(ValueError, match="unknown string measure"):
        similarity_pairs("nope", a, b)
