"""W3-12 item 6: the IBAN validator (spaces removed, upper-cased, a known country, the country's
length, ISO 7064 MOD 97-10 equal to 1), tested on published example IBANs and their mutations."""

from __future__ import annotations

import pytest

from shape.validation.iban import IBAN_LENGTHS, iban_problem, is_valid_iban

# Example IBANs published in the SWIFT IBAN Registry and in national banking documentation (the
# well-known documentation examples; none is a real account).
EXAMPLES = {
    "GB": "GB82 WEST 1234 5698 7654 32",
    "DE": "DE89 3704 0044 0532 0130 00",
    "FR": "FR14 2004 1010 0505 0001 3M02 606",
    "ES": "ES91 2100 0418 4502 0005 1332",
    "IT": "IT60 X054 2811 1010 0000 0123 456",
    "NL": "NL91 ABNA 0417 1643 00",
    "BE": "BE68 5390 0754 7034",
    "CH": "CH93 0076 2011 6238 5295 7",
    "AT": "AT61 1904 3002 3457 3201",
    "PL": "PL61 1090 1014 0000 0712 1981 2874",
    "PT": "PT50 0002 0123 1234 5678 9015 4",
    "SE": "SE45 5000 0000 0583 9825 7466",
    "NO": "NO93 8601 1117 947",
    "DK": "DK50 0040 0440 1162 43",
    "FI": "FI21 1234 5600 0007 85",
    "IE": "IE29 AIBK 9311 5212 3456 78",
    "LU": "LU28 0019 4006 4475 0000",
    "CZ": "CZ65 0800 0000 1920 0014 5399",
    "HU": "HU42 1177 3016 1111 1018 0000 0000",
    "GR": "GR16 0110 1250 0000 0001 2300 695",
    "MT": "MT84 MALT 0110 0001 2345 MTLC AST0 01S",
    "SA": "SA03 8000 0000 6080 1016 7519",
    "AE": "AE07 0331 2345 6789 0123 456",
    "TR": "TR33 0006 1005 1978 6457 8413 26",
    "BG": "BG80 BNBG 9661 1020 3456 78",
    "HR": "HR12 1001 0051 8630 0016 0",
    "RO": "RO49 AAAA 1B31 0075 9384 0000",
    "SK": "SK31 1200 0000 1987 4263 7541",
    "SI": "SI56 2633 0001 2039 086",
    "EE": "EE38 2200 2210 2014 5685",
}


def _compact(iban: str) -> str:
    return iban.replace(" ", "")


def test_the_examples_cover_at_least_ten_countries():
    assert len(EXAMPLES) >= 10


@pytest.mark.parametrize("country", sorted(EXAMPLES))
def test_published_examples_are_valid(country):
    assert is_valid_iban(EXAMPLES[country]), iban_problem(EXAMPLES[country])
    assert is_valid_iban(_compact(EXAMPLES[country]))
    assert iban_problem(EXAMPLES[country]) is None


@pytest.mark.parametrize("country", sorted(EXAMPLES))
def test_each_example_has_its_countrys_length(country):
    assert len(_compact(EXAMPLES[country])) == IBAN_LENGTHS[country]


def test_spaces_and_case_are_normalised():
    assert is_valid_iban("gb82 west 1234 5698 7654 32")
    assert is_valid_iban("  GB82WEST12345698765432  ")
    assert is_valid_iban("GB82 WEST1234 5698 7654 32") is False  # only plain spaces


def test_the_length_table_is_the_registrys():
    assert IBAN_LENGTHS["DE"] == 22
    assert IBAN_LENGTHS["NO"] == 15
    assert IBAN_LENGTHS["MT"] == 31
    assert len(IBAN_LENGTHS) >= 80
    assert all(len(c) == 2 and c.isupper() and 15 <= n <= 34 for c, n in IBAN_LENGTHS.items())


@pytest.mark.parametrize(
    ("value", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("GB", "length"),
        ("XX82WEST12345698765432", "country"),  # unknown country code
        ("ZZ0000000000000000000000", "country"),
        ("8282WEST12345698765432", "country"),  # does not start with letters
        ("GB82WEST1234569876543", "length"),  # one character short
        ("GB82WEST123456987654321", "length"),  # one character long
        ("GB82WEST12345698765433", "checksum"),  # check digits wrong
        ("GB82-WEST-1234-5698-7654-32", "characters"),
        ("GB82WEST12345698765é32", "characters"),
        ("GB00WEST12345698765432", "checksum"),  # 00 and 01, 99 are never right
        ("DE89370400440532013001", "checksum"),
    ],
)
def test_invalid_values_are_rejected_with_a_reason(value, reason):
    assert is_valid_iban(value) is False
    assert reason in (iban_problem(value) or "")


@pytest.mark.parametrize("value", [None, 12345, 1.5, b"GB82WEST12345698765432", ["GB82"]])
def test_non_text_is_not_an_iban(value):
    assert is_valid_iban(value) is False
    assert iban_problem(value) is not None


def test_boundary_the_shortest_and_longest_lengths_in_the_table():
    shortest = min(IBAN_LENGTHS, key=IBAN_LENGTHS.__getitem__)
    longest = max(IBAN_LENGTHS, key=IBAN_LENGTHS.__getitem__)
    for country in (shortest, longest):
        n = IBAN_LENGTHS[country]
        # a string of the right length with the right country is checked, not refused on length
        body = country + "00" + "0" * (n - 4)
        assert "length" not in (iban_problem(body) or "")
        assert "length" in (iban_problem(body + "0") or "")
        assert "length" in (iban_problem(body[:-1]) or "")


def _substitutions(iban: str):
    """Every replacement of one character after the country code by another of its kind."""
    for i in range(2, len(iban)):
        ch = iban[i]
        pool = "0123456789" if ch.isdigit() else "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        for other in pool:
            if other != ch:
                yield i, iban[:i] + other + iban[i + 1 :]


def _transpositions(iban: str):
    """Every swap of two adjacent different characters."""
    for i in range(len(iban) - 1):
        if iban[i] != iban[i + 1]:
            yield i, iban[:i] + iban[i + 1] + iban[i] + iban[i + 2 :]


@pytest.mark.parametrize("country", sorted(EXAMPLES))
def test_every_single_substitution_is_rejected(country):
    iban = _compact(EXAMPLES[country])
    mutants = list(_substitutions(iban))
    assert len(mutants) > 100
    survivors = [(i, m) for i, m in mutants if is_valid_iban(m)]
    assert survivors == []


def _expansion(iban: str) -> str:
    """The digit string MOD 97-10 is computed on (letters become 10 to 35)."""
    return "".join(str(int(c, 36)) for c in iban[4:] + iban[:4])


@pytest.mark.parametrize("country", sorted(EXAMPLES))
def test_every_adjacent_transposition_is_rejected_except_one_and_b(country):
    """The one transposition MOD 97-10 cannot see: swapping a "1" and a "B" (B is 11) leaves the
    digit string, and so the checksum, unchanged. Every other swap is rejected."""
    iban = _compact(EXAMPLES[country])
    mutants = list(_transpositions(iban))
    assert len(mutants) > 10
    survivors = [(i, m) for i, m in mutants if is_valid_iban(m)]
    for i, mutant in survivors:
        assert {iban[i], iban[i + 1]} == {"1", "B"}
        assert _expansion(mutant) == _expansion(iban)
    rejected = [m for _, m in mutants if not is_valid_iban(m)]
    others = [m for i, m in mutants if {iban[i], iban[i + 1]} != {"1", "B"}]
    assert all(m in rejected for m in others)


def test_the_one_and_b_swap_is_a_real_limit_of_the_checksum():
    """RO49 AAAA 1B31 ...: swapping the "1" and the "B" gives the same digit string."""
    iban = _compact(EXAMPLES["RO"])
    swapped = iban[:8] + iban[9] + iban[8] + iban[10:]
    assert swapped != iban and _expansion(swapped) == _expansion(iban)
    assert is_valid_iban(swapped)


def test_mutation_count_is_large_enough_to_mean_something():
    total = sum(
        len(list(_substitutions(_compact(v)))) + len(list(_transpositions(_compact(v))))
        for v in EXAMPLES.values()
    )
    assert total > 5000
