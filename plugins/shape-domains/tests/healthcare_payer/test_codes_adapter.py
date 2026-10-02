"""The adapter to the codes lane's assets, tested against a stand-in with the same interface.

The real assets are large downloads (``shape healthcare-codes fetch``); the lane status file records
the run against them.  Here a stand-in answers from the seed sets, with deliberate disagreements, to
show that the cross-check and the table validation find them."""

from __future__ import annotations

import datetime as dt
from types import SimpleNamespace

from shape_domains.healthcare_payer.codes_adapter import AssetCodes, FdaNdcDirectory
from shape_domains.healthcare_payer.drugs import DRUGS
from shape_domains.healthcare_payer.icd10cm import FAR, ICD10CM
from shape_domains.healthcare_payer.reference import PCS, POS
from shape_domains.healthcare_payer.services import SERVICES


class _Icd:
    def __init__(self, drop: set[str], flip_billable: set[str]) -> None:
        self.drop, self.flip = drop, flip_billable

    def get(self, code: str):
        return None if code in self.drop else SimpleNamespace(code=code)


def _fake_module(drop: set[str], flip: set[str]):
    def valid_billable(icd, code, day):
        e = ICD10CM.get(code)
        if e is None or code in drop:
            return False
        ok = e.billable and e.valid_on(day)
        return (not ok) if code in flip else ok

    return SimpleNamespace(
        icd10cm_valid_billable=valid_billable,
        normalize_code=lambda c: c.replace(".", ""),
        ndc_marketed=lambda index, ndc, day: ndc in index and index[ndc] <= day,
        NdcIndex=SimpleNamespace(
            from_table=lambda t: {r["code"]: r["valid_from"] for r in t.to_pylist()}
        ),
    )


def _assets(drop=frozenset(), flip=frozenset()):
    return AssetCodes(icd=_Icd(set(drop), set(flip)), mod=_fake_module(set(drop), set(flip)))


def test_a_seed_that_agrees_with_the_assets_has_no_problems():
    assert _assets().cross_check_seeds() == []


def test_missing_and_disagreeing_codes_are_reported():
    problems = _assets(drop={"E11.9"}, flip={"I10"}).cross_check_seeds()
    assert any("E11.9" in p and "not in the asset" in p for p in problems)
    assert any("I10" in p and "billable" in p for p in problems)


def test_hcpcs_pcs_and_pos_gaps_are_reported():
    class Set:
        def __init__(self, items):
            self.items = set(items)

        def __contains__(self, code):
            return code in self.items

        def is_valid(self, code, day, leaf_only=False):
            return code in self.items

    a = _assets()
    a.hcpcs = Set(s.hcpcs for s in SERVICES.values() if s.hcpcs and s.hcpcs != "G0463")
    a.pcs = Set(c for c in PCS if c != "0DTJ4ZZ")
    a.pos = Set(c for c in POS if c != "23")
    problems = a.cross_check_seeds()
    assert any("hcpcs G0463" in p for p in problems)
    assert any("icd10pcs 0DTJ4ZZ" in p for p in problems)
    assert any("pos 23" in p for p in problems)


def test_validate_tables_counts_invalid_diagnoses_and_unmarketed_fills(small):
    assets = _assets(drop={"E78.5"})
    assets.ndc = SimpleNamespace(
        table=SimpleNamespace(
            to_pylist=lambda: [
                {"code": f["ndc"], "valid_from": dt.date(2030, 1, 1)}
                for f in small.tables["pharmacy_claim"].to_pylist()
            ]
        )
    )
    out = assets.validate_tables(small.tables)
    assert out["icd10cm_not_valid_billable_on_date"] > 0 and "E78.5" in " ".join(out["examples"])
    assert (
        out["fills"] > 0 and out["ndc_not_marketed_on_fill_date"] == out["fills"]
    )  # all start in 2030


def test_fda_directory_matches_drugs_by_name_strength_and_form():
    rows = [
        {
            "code": "11111111101",
            "nonproprietary_name": "METFORMIN HYDROCHLORIDE",
            "proprietary_name": "Metformin",
            "dosage_form": "TABLET, FILM COATED",
            "strength": "500 mg",
            "package_units": "60",
            "valid_from": dt.date(2010, 1, 1),
            "valid_to": None,
            "labeler": "A",
        },
        {
            "code": "11111111102",
            "nonproprietary_name": "METFORMIN HYDROCHLORIDE",
            "proprietary_name": "Metformin",
            "dosage_form": "TABLET",
            "strength": "1000 mg",
            "package_units": "60",
            "valid_from": dt.date(2010, 1, 1),
            "valid_to": None,
        },
        {
            "code": "22222222201",
            "nonproprietary_name": "TIRZEPATIDE",
            "proprietary_name": "Mounjaro",
            "dosage_form": "INJECTION, SOLUTION",
            "strength": "5 mg/0.5 mL",
            "package_units": "4",
            "valid_from": dt.date(2022, 6, 1),
            "valid_to": None,
        },
    ]

    class Table:
        column_names = list(rows[0])

        def to_pylist(self):
            return rows

    d = FdaNdcDirectory(Table())
    assert [r.ndc for r in d.packages("metformin", dt.date(2023, 1, 1))] == [
        "11111111101"
    ]  # strength matched
    assert d.packages("tirzepatide", dt.date(2022, 5, 31)) == [] and d.packages(
        "tirzepatide", dt.date(2022, 6, 1)
    )
    assert d.is_marketed("11111111101", dt.date(2024, 1, 1)) and not d.is_marketed(
        "00000000000", dt.date(2024, 1, 1)
    )
    assert all(not r.interim for r in d.all_packages()) and d.source.startswith("FDA")
    assert set(d._by_drug) <= set(DRUGS) and FAR.year == 9999


def _real_assets():
    import pytest

    pytest.importorskip("shape_healthcare_codes")
    try:
        return AssetCodes.load()
    except Exception as exc:  # assets not fetched in this environment
        pytest.skip(f"codes assets not available: {exc}")


def test_seeds_agree_with_the_real_assets():
    assert _real_assets().cross_check_seeds() == []


def test_generated_tables_pass_against_the_real_assets_with_the_fda_directory():
    from shape_domains.healthcare_payer.generate import generate

    assets = _real_assets()
    if assets.ndc is None:
        import pytest

        pytest.skip("ndc asset not built")
    data = generate(300, seed=5, ndc=FdaNdcDirectory(assets.ndc.table))
    result = assets.validate_tables(data.tables)
    assert result["fills"] > 500
    assert result["icd10cm_not_valid_billable_on_date"] == 0
    assert result["age_sex_edit_violations"] == 0
    assert result["icd10pcs_not_valid"] == 0 and result["hcpcs_not_in_asset"] == []
    assert result["ndc_not_marketed_on_fill_date"] == 0
