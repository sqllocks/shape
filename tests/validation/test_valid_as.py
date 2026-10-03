"""W3-12 item 5: column validators: the ``valid_as`` contract rule, ``shape profile --validate``
and ``shape.profile(..., validators=)``."""

from __future__ import annotations

import json
import math

import pyarrow as pa
import pytest

import shape
from shape.cli.main import main
from shape.contracts.v1 import ContractError
from shape.generation import reference as gen_ref
from shape.privacy.safe_profile import SafeConfig, to_safe_profile
from shape.reference import write_pack
from shape.validation.valid_as import KINDS, ValidatorUnavailableError, measure, validator

GOOD_IBAN = "GB82 WEST 1234 5698 7654 32"


def test_the_kinds_are_the_documented_six():
    assert KINDS == ("iban", "iso3166_alpha2", "iso3166_alpha3", "iso4217", "iso639_1", "us_zip")


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv(gen_ref.REFERENCE_PATH_ENV, raising=False)
    gen_ref.clear_search_paths()
    yield
    gen_ref.clear_search_paths()


@pytest.fixture
def currency_pack(tmp_path):
    """A locally built iso-4217 pack (Shape does not ship this one)."""
    t = pa.table(
        {
            "alphabetic_code": ["EUR", "JPY", "XAU", "USD"],
            "numeric_code": ["978", "392", "959", "840"],
            "minor_units": pa.array([2, 0, None, 2], pa.int32()),
        }
    )
    write_pack(
        tmp_path / "packs" / "iso-4217",
        name="iso-4217",
        pack_version="1.0.0",
        source="test",
        retrieved="2026-10-03",
        license="test",
        attribution="test",
        transformation_version="1",
        sensitivity="public",
        tables={"iso_4217": t},
    )
    gen_ref.add_search_path(tmp_path / "packs")


# --- the value checks -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "good", "bad"),
    [
        ("iban", [GOOD_IBAN, "de89370400440532013000"], ["GB82", "", "DE89370400440532013001"]),
        (
            "iso3166_alpha2",
            ["US", "GB", "AQ", "ZW"],
            ["XX", "us", "USA", "", "U", " US", "XK", "UK"],
        ),
        ("iso3166_alpha3", ["USA", "GBR", "ATA"], ["XXX", "usa", "US", "", "USAA", "XKK"]),
        ("iso639_1", ["en", "de", "tl", "tw", "zh"], ["EN", "eng", "xx", "iw", "", "e", "en "]),
        (
            "us_zip",
            ["02872", "90210", "90210-1234", "00501"],
            [
                "9021",
                "902100",
                "0287",
                "abcde",
                "00000",
                "00000-1234",
                "90210-12",
                "90210 1234",
                "",
            ],
        ),
    ],
)
def test_value_checks(kind, good, bad):
    check = validator(kind)
    assert [v for v in good if not check(v)] == []
    assert [v for v in bad if check(v)] == []


def test_zip_codes_read_as_numbers_are_checked_as_zips_that_lost_their_zeros():
    check = validator("us_zip")
    assert check(90210) is True and check(2872) is True and check(501) is True
    assert check(90210.0) is True and check(2872.0) is True  # whole numbers held as floats
    assert [check(v) for v in (0, 0.0, -5, 100000, 2872.5, True, False, b"90210")] == [False] * 8


@pytest.mark.parametrize("kind", ["iban", "iso3166_alpha2", "iso3166_alpha3", "iso639_1"])
def test_text_validators_refuse_other_types(kind):
    check = validator(kind)
    for value in (12, 1.5, True, b"US", ["US"], {"a": 1}):
        assert check(value) is False


def test_iso_4217_needs_a_pack_that_shape_does_not_ship():
    with pytest.raises(ValidatorUnavailableError, match="iso_4217.*build_reference_packs"):
        validator("iso4217")


def test_iso_4217_with_a_local_pack(currency_pack):
    check = validator("iso4217")
    assert [check(v) for v in ("EUR", "XAU", "USD")] == [True, True, True]
    assert [check(v) for v in ("eur", "EU", "EURO", "", "XXX", 978)] == [False] * 6


def test_an_unknown_kind_lists_the_valid_ones():
    with pytest.raises(ValueError, match="iban.*us_zip"):
        validator("passport")
    with pytest.raises(ValueError):
        measure("passport", ["x"])


# --- measure ----------------------------------------------------------------------------------


def test_measure_counts_checked_valid_and_the_rate():
    out = measure("us_zip", ["02872", "90210", "9021", "00000", None, float("nan")])
    assert out == {"checked": 4, "valid": 2, "valid_rate": 0.5}
    assert list(out) == ["checked", "valid", "valid_rate"]


def test_measure_with_nothing_to_check_has_no_rate():
    assert measure("us_zip", []) == {"checked": 0, "valid": 0, "valid_rate": None}
    assert measure("us_zip", [None, math.nan]) == {"checked": 0, "valid": 0, "valid_rate": None}


def test_measure_rounds_the_rate_to_six_places():
    values = ["02872"] + ["bad"] * 2
    assert measure("us_zip", values)["valid_rate"] == pytest.approx(0.333333, abs=1e-9)


def test_measure_boundaries_all_valid_and_none_valid():
    assert measure("iso3166_alpha2", ["US"] * 3)["valid_rate"] == 1.0
    assert measure("iso3166_alpha2", ["zz"] * 3)["valid_rate"] == 0.0


# --- shape.profile(validators=) ---------------------------------------------------------------


def _table():
    return pa.table(
        {
            "zip": ["02872", "10001", "9021", "00000", "90210", None],
            "country": ["US", "GB", "XX", "us", "DE", "FR"],
            "iban": [GOOD_IBAN, GOOD_IBAN, "GB00", GOOD_IBAN, GOOD_IBAN, GOOD_IBAN],
            "n": [1, 2, 3, 4, 5, 6],
        }
    )


def test_profile_stores_validators_on_the_column():
    prof = shape.profile(
        _table(),
        validators={"zip": "us_zip", "country": ["iso3166_alpha2", "iso3166_alpha3"]},
    )
    cols = next(iter(prof.tables.values()))["columns"]
    assert cols["zip"]["validators"] == {"us_zip": {"checked": 5, "valid": 3, "valid_rate": 0.6}}
    assert cols["country"]["validators"]["iso3166_alpha2"] == {
        "checked": 6,
        "valid": 4,
        "valid_rate": 0.666667,
    }
    assert cols["country"]["validators"]["iso3166_alpha3"]["valid"] == 0
    assert "validators" not in cols["iban"] and "validators" not in cols["n"]


def test_a_profile_without_validators_has_no_validators_key():
    prof = shape.profile(_table())
    assert all("validators" not in c for c in next(iter(prof.tables.values()))["columns"].values())


def test_validators_store_counts_and_rates_only():
    prof = shape.profile(_table(), validators={"iban": "iban"})
    entry = next(iter(prof.tables.values()))["columns"]["iban"]["validators"]["iban"]
    assert set(entry) == {"checked", "valid", "valid_rate"}
    assert all(isinstance(v, (int, float)) for v in entry.values())


def test_validators_are_saved_with_the_profile(tmp_path):
    prof = shape.profile(_table(), validators={"zip": "us_zip"})
    shape.save(prof, str(tmp_path / "p.shape"))
    back = shape.load(str(tmp_path / "p.shape"))
    assert next(iter(back.tables.values()))["columns"]["zip"]["validators"]["us_zip"]["valid"] == 3


def test_validators_enter_the_share_safe_profile():
    prof = shape.profile(_table(), validators={"zip": "us_zip"})
    safe = to_safe_profile(prof, SafeConfig()).to_dict()
    col = next(iter(safe["tables"].values()))["columns"]["zip"]
    assert col["validators"] == {"us_zip": {"checked": 5, "valid": 3, "valid_rate": 0.6}}
    assert "9021" not in json.dumps(col["validators"])


def test_profile_validators_for_a_dataset_are_given_per_table():
    prof = shape.profile(
        {"a": _table(), "b": _table()},
        validators={"a": {"zip": "us_zip"}},
    )
    assert "validators" in prof.tables["a"]["columns"]["zip"]
    assert "validators" not in prof.tables["b"]["columns"]["zip"]
    with pytest.raises(ValueError, match="table"):
        shape.profile({"a": _table()}, validators={"zip": "us_zip"})
    with pytest.raises(ValueError, match="'nope'"):
        shape.profile({"a": _table()}, validators={"nope": {"zip": "us_zip"}})


@pytest.mark.parametrize(
    ("validators", "match"),
    [
        ({"missing": "us_zip"}, "no column 'missing'"),
        ({"zip": "passport"}, "passport"),
        ({"zip": 3}, "kind"),
        ({"zip": []}, "kind"),
        ({"zip": ["us_zip", 3]}, "kind"),
        ("zip=us_zip", "validators"),
    ],
)
def test_bad_validators_are_refused(validators, match):
    with pytest.raises(ValueError, match=match):
        shape.profile(_table(), validators=validators)


def test_a_numeric_column_is_checked_as_numbers():
    t = pa.table({"zip": [90210, 2872, 0, 100000, None]})
    prof = shape.profile(t, validators={"zip": "us_zip"})
    entry = next(iter(prof.tables.values()))["columns"]["zip"]["validators"]["us_zip"]
    assert entry == {"checked": 4, "valid": 2, "valid_rate": 0.5}


def test_iso_4217_validator_in_a_profile(currency_pack):
    t = pa.table({"ccy": ["EUR", "USD", "ZZZ", "eur"]})
    prof = shape.profile(t, validators={"ccy": "iso4217"})
    entry = next(iter(prof.tables.values()))["columns"]["ccy"]["validators"]["iso4217"]
    assert entry == {"checked": 4, "valid": 2, "valid_rate": 0.5}


def test_iso_4217_validator_without_the_pack_is_an_error_in_a_profile():
    with pytest.raises(ValidatorUnavailableError):
        shape.profile(pa.table({"c": ["EUR"]}), validators={"c": "iso4217"})


# --- the contract rule ------------------------------------------------------------------------


@pytest.fixture(scope="module")
def profile():
    return shape.profile(
        _table(),
        validators={"zip": "us_zip", "country": "iso3166_alpha2", "iban": "iban"},
    )


def test_the_rule_passes_at_or_below_the_measured_rate(profile):
    contract = {"columns": {"zip": {"valid_as": {"kind": "us_zip", "min_valid_rate": 0.6}}}}
    assert shape.check(profile, contract).passed


def test_the_rule_fails_above_the_measured_rate_and_says_what_was_seen(profile):
    contract = {"columns": {"zip": {"valid_as": {"kind": "us_zip", "min_valid_rate": 0.61}}}}
    result = shape.check(profile, contract)
    assert not result.passed
    (v,) = result.violations
    assert v["column"] == "zip" and v["rule"] == "valid_as"
    assert v["expected"] == {"kind": "us_zip", "min_valid_rate": 0.61}
    assert v["observed"] == {"checked": 5, "valid": 3, "valid_rate": 0.6}


def test_the_rate_defaults_to_one(profile):
    contract = {"columns": {"zip": {"valid_as": {"kind": "us_zip"}}}}
    (v,) = shape.check(profile, contract).violations
    assert v["expected"] == {"kind": "us_zip", "min_valid_rate": 1.0}
    clean = shape.profile(pa.table({"z": ["02872", "10001"]}), validators={"z": "us_zip"})
    assert shape.check(clean, {"columns": {"z": {"valid_as": {"kind": "us_zip"}}}}).passed


def test_a_rate_of_zero_always_passes_a_measured_column(profile):
    contract = {
        "columns": {"country": {"valid_as": {"kind": "iso3166_alpha2", "min_valid_rate": 0}}}
    }
    assert shape.check(profile, contract).passed


def test_a_rule_with_no_measurement_is_a_violation_that_says_not_measured(profile):
    no_kind = {"columns": {"zip": {"valid_as": {"kind": "iso3166_alpha2"}}}}
    (v,) = shape.check(profile, no_kind).violations
    assert "not measured" in v["observed"] and "--validate zip=iso3166_alpha2" in v["observed"]
    bare = shape.profile(_table())
    contract = {"columns": {"zip": {"valid_as": {"kind": "us_zip"}}}}
    (v,) = shape.check(bare, contract).violations
    assert v["rule"] == "valid_as" and "not measured" in v["observed"]


def test_a_column_with_nothing_to_check_passes_the_rule():
    prof = shape.profile(
        pa.table({"z": pa.array([None, None], pa.string())}), validators={"z": "us_zip"}
    )
    assert shape.check(prof, {"columns": {"z": {"valid_as": {"kind": "us_zip"}}}}).passed


def test_the_rule_in_a_dataset_contract(profile):
    ds = shape.profile({"t": _table()}, validators={"t": {"zip": "us_zip"}})
    contract = {"tables": {"t": {"columns": {"zip": {"valid_as": {"kind": "us_zip"}}}}}}
    (v,) = shape.check(ds, contract).violations
    assert v["column"] == "t.zip"


@pytest.mark.parametrize(
    "rule",
    [
        "us_zip",
        {},
        {"kind": "passport"},
        {"kind": 3},
        {"kind": "us_zip", "min_valid_rate": 1.5},
        {"kind": "us_zip", "min_valid_rate": -0.1},
        {"kind": "us_zip", "min_valid_rate": "1"},
        {"kind": "us_zip", "min_valid_rate": True},
        {"kind": "us_zip", "min_rate": 1},
        [{"kind": "us_zip"}],
    ],
)
def test_a_malformed_rule_is_a_contract_error(rule, profile):
    with pytest.raises(ContractError, match="valid_as"):
        shape.check(profile, {"columns": {"zip": {"valid_as": rule}}})


def test_the_rate_boundaries_are_accepted(profile):
    for r in (0, 1, 0.0, 1.0):
        shape.check(
            profile, {"columns": {"zip": {"valid_as": {"kind": "us_zip", "min_valid_rate": r}}}}
        )


# --- shape profile --validate -----------------------------------------------------------------


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "d.csv"
    path.write_text(
        "zip,country\n02872,US\n10001,GB\n9021,XX\nABCDE,us\n90210,DE\n", encoding="utf-8"
    )
    return path


def _saved(path):
    return next(iter(shape.load(str(path)).tables.values()))["columns"]


def test_the_cli_stores_validators(csv_file, tmp_path, capsys):
    out = tmp_path / "p.shape"
    code, _, _ = run(
        capsys,
        "profile",
        str(csv_file),
        "-o",
        str(out),
        "--validate",
        "zip=us_zip",
        "--validate",
        "country=iso3166_alpha2",
        "--validate",
        "country=iso3166_alpha3",
    )
    assert code == 0
    cols = _saved(out)
    assert cols["zip"]["validators"]["us_zip"]["valid"] == 3  # a text column: the zeros are kept
    assert set(cols["country"]["validators"]) == {"iso3166_alpha2", "iso3166_alpha3"}


@pytest.mark.parametrize(
    ("arg", "match"),
    [
        ("zip", "COLUMN=KIND"),
        ("=us_zip", "COLUMN=KIND"),
        ("zip=", "COLUMN=KIND"),
        ("zip=passport", "passport"),
        ("nope=us_zip", "no column 'nope'"),
    ],
)
def test_the_cli_refuses_a_bad_validate(csv_file, tmp_path, capsys, arg, match):
    code, _, err = run(
        capsys, "profile", str(csv_file), "-o", str(tmp_path / "p.shape"), "--validate", arg
    )
    assert code == 2 and match in err
    assert not (tmp_path / "p.shape").exists()


def test_the_cli_refuses_validate_for_several_tables(tmp_path, capsys):
    d = tmp_path / "data"
    d.mkdir()
    (d / "a.csv").write_text("zip\n02872\n", encoding="utf-8")
    (d / "b.csv").write_text("zip\n02872\n", encoding="utf-8")
    code, _, err = run(
        capsys,
        "profile",
        str(d),
        "--dataset",
        "-o",
        str(tmp_path / "p.shape"),
        "--validate",
        "zip=us_zip",
    )
    assert code == 2 and "single table" in err


def test_the_cli_validate_feeds_the_contract_rule(csv_file, tmp_path, capsys):
    out = tmp_path / "p.shape"
    assert main(["profile", str(csv_file), "-o", str(out), "--validate", "zip=us_zip"]) == 0
    capsys.readouterr()
    contract = tmp_path / "c.json"
    contract.write_text(json.dumps({"columns": {"zip": {"valid_as": {"kind": "us_zip"}}}}))
    code, out_text, _ = run(capsys, "check", str(out), str(contract))
    assert code != 0
    assert "valid_as" in out_text
    contract.write_text(
        json.dumps({"columns": {"zip": {"valid_as": {"kind": "us_zip", "min_valid_rate": 0.6}}}})
    )
    code, _, _ = run(capsys, "check", str(out), str(contract))
    assert code == 0
