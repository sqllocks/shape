"""NPI check digit (CMS 2004 requirements document) and the synthetic never-assigned range."""

import pytest
from shape_healthcare_codes.npi import (
    check_digit,
    generate_synthetic_npis,
    is_real_format_npi,
    is_synthetic_npi,
    luhn_valid,
)


def test_cms_worked_example():
    # The CMS document: identifier 123456789 gives check digit 3, so NPI 1234567893.
    assert check_digit("123456789") == 3
    assert luhn_valid("1234567893")
    assert not luhn_valid("1234567894")


def test_check_digit_matches_an_independent_luhn_over_the_prefixed_number():
    def luhn(s: str) -> bool:
        total = 0
        for i, ch in enumerate(reversed(s)):
            d = int(ch)
            if i % 2 == 1:
                d = d * 2 - 9 if d * 2 > 9 else d * 2
            total += d
        return total % 10 == 0

    for body in ("100000000", "199999999", "234567890", "987654321", "000000000"):
        assert luhn("80840" + body + str(check_digit(body)))


def test_rejects_bad_shapes():
    for bad in ("", "123456789", "12345678930", "abcdefghij", "1234 56789"):
        assert not luhn_valid(bad)
    with pytest.raises(ValueError):
        check_digit("12345")


def test_synthetic_npis_are_valid_but_never_a_real_first_digit():
    npis = generate_synthetic_npis(5000, seed=7)
    assert len(set(npis)) == 5000
    assert all(luhn_valid(n) and is_synthetic_npi(n) for n in npis)
    assert all(n[0] not in "12" for n in npis)
    assert not any(is_real_format_npi(n) for n in npis)
    assert is_real_format_npi("1234567893")
    assert not is_synthetic_npi("1234567893")


def test_synthetic_generation_is_deterministic_and_seeded():
    assert generate_synthetic_npis(50, seed=1) == generate_synthetic_npis(50, seed=1)
    assert generate_synthetic_npis(50, seed=1) != generate_synthetic_npis(50, seed=2)
    with pytest.raises(ValueError):
        generate_synthetic_npis(10**7 + 1)
