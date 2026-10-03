import datetime as dt
import zipfile
from pathlib import Path

import pytest
from fixtures import CM_R1, CM_R2, CM_R3, CM_TABULAR, order_text, zip_bytes
from shape_healthcare_codes import store
from shape_healthcare_codes.builders import icd10cm as m
from shape_healthcare_codes.builders._releases import parse_order, read_text
from shape_healthcare_codes.model import Release, fy_start
from shape_healthcare_codes.validators import icd10cm_valid_billable

D = dt.date


def _table():
    per = [
        (Release("FY2020", D(2019, 10, 1)), parse_order(order_text(CM_R1))),
        (Release("FY2021", D(2020, 10, 1)), parse_order(order_text(CM_R2))),
        (Release("FY2022", D(2021, 10, 1)), parse_order(order_text(CM_R3))),
    ]
    return m.build_table(per, m.parse_tabular(CM_TABULAR))


def test_parse_order_reads_the_fixed_columns():
    rows = parse_order(order_text(CM_R1))
    assert len(rows) == len(CM_R1)
    assert rows[1].code == "A009" and rows[1].leaf and rows[0].leaf is False
    assert rows[1].short_desc == "Cholera, unspecified"
    assert rows[5].long_desc.endswith("initial encounter for closed fracture")


def test_parse_order_refuses_a_line_out_of_layout_instead_of_skipping_it():
    with pytest.raises(ValueError, match="line 2"):
        parse_order(order_text(CM_R1[:1]) + "\r\nthis is not an order line\r\n")


def test_validity_changes_at_the_release_boundary():
    cs = store.CodeSet("icd10cm", _table())
    # M54.5 was billable, became a header when M54.50 was added (the real October 2021 change)
    assert icd10cm_valid_billable(cs, "M54.5", D(2020, 10, 1)) is False  # R2: header already
    assert icd10cm_valid_billable(cs, "M54.5", D(2019, 10, 1)) is True
    assert icd10cm_valid_billable(cs, "M54.50", D(2020, 9, 30)) is False
    assert icd10cm_valid_billable(cs, "M54.50", D(2020, 10, 1)) is True
    assert icd10cm_valid_billable(cs, "U07.1", D(2019, 12, 31)) is False
    assert icd10cm_valid_billable(cs, "U07.1", D(2020, 10, 1)) is True
    assert icd10cm_valid_billable(cs, "A00.9", D(2021, 9, 30)) is True
    assert icd10cm_valid_billable(cs, "A00.9", D(2021, 10, 1)) is False  # retired
    assert icd10cm_valid_billable(cs, "A00.90", D(2021, 10, 1)) is True
    assert icd10cm_valid_billable(cs, "E11", D(2021, 10, 1)) is False  # header, not billable
    assert icd10cm_valid_billable(cs, "E11.9", D(2021, 10, 1)) is True


def test_valid_from_and_valid_to_follow_the_releases():
    cs = store.CodeSet("icd10cm", _table())
    old = cs.get("A009")
    assert old is not None and old.valid_from == D(2019, 10, 1)
    assert old.valid_to == D(2021, 9, 30)  # the day before the release that dropped it
    new = cs.get("A0090")
    assert new is not None and new.valid_to is None and new.valid_from == D(2021, 10, 1)


def test_chapter_block_category_and_text():
    cs = store.CodeSet("icd10cm", _table())
    r = cs.get("E119")
    assert r is not None
    assert r.attrs["category"] == "E11" and r.attrs["chapter"] == 4
    assert r.attrs["block"] == "E08-E13" and r.attrs["block_desc"] == "Diabetes mellitus"
    assert r.attrs["chapter_desc"] == "Endocrine, nutritional and metabolic diseases"
    assert r.long_desc == "Type 2 diabetes mellitus without complications"


def test_seventh_character_comes_from_the_stem_not_from_the_code():
    cs = store.CodeSet("icd10cm", _table())
    fx = cs.get("S72001A")
    assert fx is not None
    assert fx.attrs["ext7"] == "A"
    assert fx.attrs["ext7_meaning"] == "initial encounter for closed fracture"
    assert fx.attrs["laterality"] == "right"
    # a 7-character neoplasm code has no extension: its stem has no 7th-character definitions
    ca = cs.get("C441021")
    assert ca is not None and ca.attrs["ext7"] is None and ca.attrs["ext7_meaning"] is None
    assert ca.attrs["laterality"] == "right"


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("Fracture of neck of right femur", "right"),
        ("Injury of left knee", "left"),
        ("Bilateral cataract", "bilateral"),
        ("Cataract, unspecified eye", "unspecified"),
        ("Right or left ventricular failure with right and left sides", None),
        ("Type 2 diabetes mellitus", None),
        ("bilateral, right and left", "bilateral"),
    ],
)
def test_laterality_from_text(text, want):
    assert m.laterality_of(text) == want


def test_releases_must_be_oldest_first():
    rows = parse_order(order_text(CM_R1))
    with pytest.raises(ValueError, match="oldest first"):
        m.build_table([(Release("b", D(2021, 1, 1)), rows), (Release("a", D(2020, 1, 1)), rows)])


def test_subset_keeps_validity_history(tmp_path):
    t = m.subset_codes(_table(), ["E11", "U07"])
    assert sorted(t.column("code").to_pylist()) == ["E11", "E119", "U071"]
    path = store.write_asset("icd10cm", t, {"release": "x"}, tmp_path)
    cs = store.load("icd10cm", tmp_path)
    assert path.is_file() and cs.is_valid("U071", D(2021, 1, 1), leaf_only=True)
    assert not cs.is_valid("U071", D(2019, 12, 1))


def test_read_text_picks_the_order_file_not_the_addenda(tmp_path):
    z = tmp_path / "x.zip"
    z.write_bytes(
        zip_bytes(
            {
                "tabular-order/icd10cm_codes_2021.txt": "codes file",
                "tabular-order/icd10cm_order_addenda_2021.txt": "Add: addenda",
                "tabular-order/icd10cm_order_2021.txt": order_text(CM_R1[:1]),
            }
        )
    )
    assert "Cholera" in read_text(z, m._O)
    with zipfile.ZipFile(z) as zf:
        assert len(zf.namelist()) == 3


def test_build_from_local_files_end_to_end(tmp_path: Path):
    files: dict[str, Path] = {}
    texts = {"FY2016": CM_R1, "FY2027": CM_R3}
    for src in m.RELEASES:
        rows = texts.get(src.id, CM_R2)
        p = tmp_path / f"{src.id}.txt"
        p.write_text(order_text(rows), encoding="utf-8")
        files[src.id] = p
    tab = tmp_path / "tab.zip"
    tab.write_bytes(zip_bytes({"icd10cm-tabular_-2027.xml": CM_TABULAR}))
    files["tabular"] = tab
    # the plain-text release files are read as text; zipped ones are written as zips
    for src in m.RELEASES:
        if src.member is not None:
            z = tmp_path / f"{src.id}.zip"
            z.write_bytes(zip_bytes({"icd10cm-order-2027.txt": files[src.id].read_text("utf-8")}))
            files[src.id] = z
    table, manifest = m.build(from_files=files)
    assert manifest["releases"][0] == "FY2016" and manifest["releases"][-1] == "FY2027"
    assert len(manifest["releases"]) == len(m.RELEASES) == 15
    cs = store.CodeSet("icd10cm", table)
    assert cs.is_valid("E11.9", fy_start(2027), leaf_only=True)
    assert not cs.is_valid("A00.9", fy_start(2027))
    assert manifest["subset"] is False and "tabular" in manifest["sources"]


def test_every_release_has_a_pin_and_a_distinct_date():
    assert set(m.PINS) == {r.id for r in m.RELEASES} | {"tabular"}
    assert all(p.startswith("sha256:") and len(p) == 71 for p in m.PINS.values())
    dates = [r.effective for r in m.RELEASES]
    assert dates == sorted(dates) and len(set(dates)) == len(dates)
