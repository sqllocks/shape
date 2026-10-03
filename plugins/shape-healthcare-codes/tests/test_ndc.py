import pytest
from shape_healthcare_codes.ndc import AmbiguousNdc, format_ndc11, normalize_ndc


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("0002-3227-30", "00002322730"),  # 4-4-2
        ("12345-678-90", "12345067890"),  # 5-3-2
        ("12345-6789-1", "12345678901"),  # 5-4-1
        ("00002-3227-30", "00002322730"),  # already 5-4-2
        ("00002322730", "00002322730"),
    ],
)
def test_normalizes_to_5_4_2(raw, want):
    assert normalize_ndc(raw) == want


def test_bare_ten_digits_need_a_layout():
    with pytest.raises(AmbiguousNdc):
        normalize_ndc("0002322730")
    assert normalize_ndc("0002322730", layout="4-4-2") == "00002322730"
    assert normalize_ndc("1234567890", layout="5-3-2") == "12345067890"
    assert normalize_ndc("1234567890", layout="5-4-1") == "12345678900"
    with pytest.raises(ValueError):
        normalize_ndc("1234567890", layout="6-3-1")


def test_rejects_non_ndc():
    for bad in ("", "abc", "12-34-56", "123456-789-01", "0002-3227", "1234567890123"):
        assert normalize_ndc(bad) is None


def test_format_round_trip():
    assert format_ndc11("00002322730") == "00002-3227-30"
    with pytest.raises(ValueError):
        format_ndc11("123")
