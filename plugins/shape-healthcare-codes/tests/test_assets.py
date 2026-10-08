"""The shipped data, the asset catalog and THIRD_PARTY_NOTICES.md agree."""

import datetime as dt
from pathlib import Path

import shape_healthcare_codes
from shape_healthcare_codes import store
from shape_healthcare_codes.builders import BUILDERS
from shape_healthcare_codes.builders.icd10cm import STARTER_PREFIXES
from shape_healthcare_codes.provenance import all_assets
from shape_healthcare_codes.validators import icd10cm_valid_billable

PLUGIN = Path(shape_healthcare_codes.__file__).resolve().parents[2]
NOTICES = (PLUGIN / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
D = dt.date
READ_ON = {"hcc_hierarchy": "2026-10-04", "hcc_coefficients": "2026-10-04"}


def test_every_asset_has_a_notice_a_licence_quote_and_a_mode_that_matches_its_builder():
    for a in all_assets().values():
        assert a.title in NOTICES, f"{a.id}: {a.title!r} is not in THIRD_PARTY_NOTICES.md"
        assert a.licence and a.licence_url and a.licence_quote and a.source_url
        # the day the licence was read: the risk-adjustment tables were added (and their pages
        # read) on 2026-10-04, every other asset on 2026-10-02
        assert a.checked_on == READ_ON.get(a.id, "2026-10-02"), a.id
        assert a.mode in ("shipped", "fetch", "byo")
        assert (a.id in BUILDERS) == (a.mode != "byo"), f"{a.id}: mode {a.mode} vs builder"


def test_licensed_sets_have_no_builder_and_no_shipped_file():
    byo = {a.id for a in all_assets().values() if a.mode == "byo"}
    assert {
        "cpt",
        "snomed",
        "revenue_codes",
        "type_of_bill",
        "nucc_taxonomy",
        "carc",
        "rarc",
    } <= byo
    assert {"icd10_who", "icd10gm", "icd10am"} <= byo
    shipped = {p.stem for p in store.SHIPPED_DIR.glob("*.arrow")}
    assert shipped == {"icd10cm"}  # nothing licensed, nothing without a quoted licence
    assert not (byo & shipped) and not (byo & set(BUILDERS))


def test_the_shipped_subset_is_a_labelled_subset_of_a_public_domain_set():
    meta = store.manifest("icd10cm", store.SHIPPED_DIR)
    assert meta["subset"] is True and meta["release"].startswith("FY2027")
    assert all_assets()["icd10cm"].licence.startswith("public domain")
    assert "Source: CDC/NCHS" in NOTICES
    cs = store.load("icd10cm", store.SHIPPED_DIR)
    prefixes = tuple(STARTER_PREFIXES)
    assert all(c.startswith(prefixes) for c in cs.column("code").to_pylist())


def test_the_wheel_data_stays_small():
    total = sum(p.stat().st_size for p in store.SHIPPED_DIR.iterdir())
    assert total < 1_000_000, f"shipped data is {total} bytes; ship a smaller subset"


def test_shipped_subset_answers_the_acceptance_questions():
    cs = store.load("icd10cm", store.SHIPPED_DIR)
    today = D(2026, 10, 2)
    assert icd10cm_valid_billable(cs, "E11.9", today)
    assert not icd10cm_valid_billable(cs, "E11", today)  # a header
    assert icd10cm_valid_billable(cs, "I10", today)
    assert icd10cm_valid_billable(cs, "S72.001A", today)
    assert icd10cm_valid_billable(cs, "E11.65", D(2016, 1, 1))
    assert not icd10cm_valid_billable(cs, "E11.65", D(2015, 9, 30))  # before ICD-10-CM
    # M54.5 was billable until 2021-09-30 and a header after (M54.50, M54.51, M54.59 replace it)
    assert icd10cm_valid_billable(cs, "M54.5", D(2021, 9, 30))
    assert not icd10cm_valid_billable(cs, "M54.5", D(2021, 10, 1))
    assert icd10cm_valid_billable(cs, "M54.50", D(2021, 10, 1))
    assert not icd10cm_valid_billable(cs, "M54.50", D(2021, 9, 30))
    # U07.1 appears in the FY2021 file (the April 2020 release has no pinnable file: documented)
    assert icd10cm_valid_billable(cs, "U07.1", D(2020, 10, 1))
    r = cs.get("E11.9")
    assert r is not None and r.attrs["chapter"] == 4 and r.attrs["block"] == "E08-E13"
    fx = cs.get("S72.001A")
    assert fx is not None
    assert fx.attrs["laterality"] == "right" and fx.attrs["ext7"] == "A"


def test_codes_on_a_date_are_all_valid_and_billable_that_day():
    cs = store.load("icd10cm", store.SHIPPED_DIR)
    for day in (D(2016, 3, 1), D(2021, 12, 1), D(2023, 5, 1), D(2026, 10, 2)):
        codes = cs.codes_on(day).to_pylist()
        assert len(codes) > 1000
        assert all(cs.is_valid(c, day, leaf_only=True) for c in codes[::50])
    assert len(cs.codes_on(D(2015, 9, 30))) == 0


def test_the_notices_travel_inside_the_wheel():
    import tomllib

    meta = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))
    assert "THIRD_PARTY_NOTICES.md" in meta["project"]["license-files"]
    assert "data/*.arrow" in meta["tool"]["setuptools"]["package-data"]["shape_healthcare_codes"]
