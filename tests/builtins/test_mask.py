"""P6-03: the ``mask`` transform and ``shape mask``.

Each deliberate difference from the baseline masker has a test that fails on the baseline's
behaviour (named MASK-A1 .. MASK-A5 in ``benchmarks/vs_spindle/mask_1to1/verify.py``).
"""

from __future__ import annotations

import json
import re

import numpy as np
import pyarrow as pa
import pyarrow.csv as pacsv
import pyarrow.parquet as pq
import pytest

from shape.builtins.transforms import Mask, MaskConfig, MaskError, mask_tables
from shape.builtins.transforms import _mask_values as mv
from shape.cli.main import main

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def people(n: int = 400) -> pa.Table:
    rng = np.random.default_rng(3)
    return pa.table(
        {
            "customer_id": list(range(1, n + 1)),
            "email": [f"user{i}@corp.example" for i in range(n)],
            "phone": [
                f"({rng.integers(200, 999)}) {rng.integers(200, 999)}-{i % 9000 + 1000}"
                for i in range(n)
            ],
            "zip4": [f"{rng.integers(10000, 99999)}-{rng.integers(1000, 9999)}" for _ in range(n)],
            "ip_address": [".".join(str(v) for v in rng.integers(1, 255, 4)) for _ in range(n)],
            "last_login": [f"2022-0{1 + i % 9}-1{i % 9} 10:11:12" for i in range(n)],
            "status": ["active", "closed"] * (n // 2),
        }
    )


def test_registered_as_a_builtin_transform():
    from importlib.metadata import entry_points

    from shape.plugins.api.v1 import Transform

    names = {ep.name for ep in entry_points(group="shape.transforms")}
    assert "mask" in names
    assert isinstance(Mask(), Transform)


def test_masks_what_the_baseline_masks_in_the_same_format():
    t = people()
    res = mask_tables({"t": t})
    assert res.columns_masked["t"] == ["email", "phone", "zip4", "ip_address"]
    out = res.tables["t"]
    assert out.schema == t.schema
    assert all(EMAIL.match(v) for v in out["email"].to_pylist())
    assert all(re.fullmatch(r"\(\d{3}\) \d{3}-\d{4}", v) for v in out["phone"].to_pylist())
    assert all(re.fullmatch(r"\d{5}-\d{4}", v) for v in out["zip4"].to_pylist())
    for v in out["ip_address"].to_pylist():
        parts = v.split(".")
        assert len(parts) == 4 and all(0 <= int(p) <= 255 for p in parts)
    for name in ("customer_id", "status", "last_login"):
        assert out[name].equals(t[name])


def test_no_original_value_remains():
    t = people()
    out = mask_tables({"t": t}).tables["t"]
    for name in ("email", "phone", "zip4", "ip_address"):
        before, after = t[name].to_pylist(), out[name].to_pylist()
        assert all(a != b for a, b in zip(before, after, strict=True))
        assert not set(before) & set(after)


def test_timestamp_column_named_like_a_username_is_not_masked():  # MASK-A1
    t = pa.table({"last_login": ["2021-12-26 06:11:39", "2022-01-02 10:00:00"]})
    assert mask_tables({"t": t}).columns_masked["t"] == []
    typed = pa.table({"last_login": pa.array([1, 2], pa.timestamp("s"))})
    assert mask_tables({"t": typed}).columns_masked["t"] == []


def test_a_phone_keeps_its_layout_and_a_zip_plus_4_stays_one():  # MASK-A2
    t = pa.table(
        {
            "phone": ["+44 20 7946 0958", "(387) 306-3453", "538.990.8386", "555-0100 x12"],
            "zip4": ["68307-9188", "16515-4473", "20067-5806", "11111-2222"],
        }
    )
    out = mask_tables({"t": t}).tables["t"]
    for before, after in zip(t["phone"].to_pylist(), out["phone"].to_pylist(), strict=True):
        assert re.sub(r"\d", "9", before) == re.sub(r"\d", "9", after) or before.startswith("+")
    assert out["phone"][0].as_py().startswith("+44 ")  # the country code stays
    assert out["phone"][3].as_py().endswith(" x" + out["phone"][3].as_py()[-2:])  # `x` stays
    assert all(re.fullmatch(r"\d{5}-\d{4}", v) for v in out["zip4"].to_pylist())


def test_ip_address_is_not_taken_for_a_street_address():  # MASK-A3
    assert mv.type_from_name("ip_address") == "ip_address"
    assert mv.type_from_name("ipAddress") == "ip_address"
    assert mv.type_from_name("ipaddress") == "ip_address"
    assert mv.type_from_name("street_address") == "address"
    assert mv.type_from_name("home_address") == "address"


def test_first_name_is_a_first_name_not_a_full_name():  # MASK-A4
    assert mv.type_from_name("first_name") == "first_name"
    assert mv.type_from_name("FirstName") == "first_name"
    assert mv.type_from_name("last_name") == "last_name"
    t = pa.table({"first_name": ["Ann", "Bob", "Carla"] * 10})
    out = mask_tables({"t": t}).tables["t"]
    assert all(" " not in v for v in out["first_name"].to_pylist())


def test_categorical_replacements_differ_from_the_original_cell():  # MASK-A5
    states = ["CA", "TX", "NY", "FL", "IL", "PA", "OH", "GA", "NC", "MI"] * 300
    t = pa.table({"state": states})
    out = mask_tables({"t": t}).tables["t"]
    assert all(a != b for a, b in zip(states, out["state"].to_pylist(), strict=True))
    assert all(len(v) == 2 for v in out["state"].to_pylist())


def test_names_that_only_contain_a_keyword_are_not_masked():
    for name in ("description", "business", "status", "estate", "filename_ext", "zipper"):
        assert mv.type_from_name(name) is None, name
    assert mv.type_from_name("customeremail") == "email"
    assert mv.type_from_name("shipZipCode") == "zip"
    assert mv.type_from_name("ZIP4") == "zip"


def test_nulls_stay_and_the_same_value_gets_the_same_replacement():
    t = pa.table({"email": ["a@x.com", None, "b@x.com", "a@x.com", None]})
    out = mask_tables({"t": t}).tables["t"]["email"].to_pylist()
    assert [v is None for v in out] == [False, True, False, False, True]
    assert out[0] == out[3] and out[0] != out[2]


def test_unique_columns_stay_unique():
    t = pa.table({"email": [f"p{i}@x.com" for i in range(5000)]})
    out = mask_tables({"t": t}).tables["t"]["email"].to_pylist()
    assert len(set(out)) == 5000


def test_deterministic_for_a_seed_and_different_for_another():
    t = {"t": people(100)}
    a = mask_tables(t, MaskConfig(seed=5)).tables["t"]
    b = mask_tables(t, MaskConfig(seed=5)).tables["t"]
    c = mask_tables(t, MaskConfig(seed=6)).tables["t"]
    assert a.equals(b)
    assert not a["email"].equals(c["email"])


def test_a_key_and_the_columns_that_refer_to_it_stay_joined():
    parents = pa.table(
        {"ssn": [f"{100 + i:03d}-{10 + i % 80:02d}-{1000 + i:04d}" for i in range(50)]}
    )
    ssn = parents["ssn"].to_pylist()
    children = pa.table(
        {
            "claim_id": list(range(200)),
            "ssn": [ssn[i % 50] for i in range(190)] + ["999-99-9999"] * 10,  # ten orphans
        }
    )
    res = mask_tables({"people": parents, "claims": children})
    new_parent = set(res.tables["people"]["ssn"].to_pylist())
    new_child = res.tables["claims"]["ssn"].to_pylist()
    assert all(v in new_parent for v in new_child[:190])
    orphans = new_child[190:]
    assert "999-99-9999" not in orphans  # an orphan is masked too, not kept
    assert not set(new_child) & set(ssn)


def test_a_foreign_key_to_a_masked_key_is_masked_whatever_its_name():
    parents = pa.table(
        {"ssn": [f"{100 + i:03d}-{10 + i % 80:02d}-{1000 + i:04d}" for i in range(50)]}
    )
    ssn = parents["ssn"].to_pylist()
    children = pa.table({"owner": [ssn[i % 50] for i in range(100)], "amount": [1.0] * 100})
    res = mask_tables({"people": parents, "claims": children})
    assert res.columns_masked["claims"] == ["owner"]
    assert not set(res.tables["claims"]["owner"].to_pylist()) & set(ssn)


def test_integer_columns_keep_their_type_and_width():
    t = pa.table({"zip": pa.array([2134, 10001, 90210, None], pa.int64()), "age": [1, 2, 3, 4]})
    out = mask_tables({"t": t}).tables["t"]
    assert out["zip"].type == pa.int64()
    assert out["zip"][3].as_py() is None
    assert [len(str(v)) for v in out["zip"].to_pylist()[:3]] == [4, 5, 5]
    assert out["age"].equals(t["age"])


def test_card_numbers_stay_luhn_valid_and_ibans_stay_valid():
    cards = ["4539 1488 0343 6467", "4111 1111 1111 1111", "5500 0000 0000 0004"]
    ibans = ["DE89 3704 0044 0532 0130 00", "GB29 NWBK 6016 1331 9268 19"]
    out = mask_tables({"t": pa.table({"credit_card": cards}), "u": pa.table({"iban": ibans})})
    for v in out.tables["t"]["credit_card"].to_pylist():
        assert re.fullmatch(r"\d{4} \d{4} \d{4} \d{4}", v)
        assert mv._luhn_ok(v.replace(" ", ""))
    for orig, v in zip(ibans, out.tables["u"]["iban"].to_pylist(), strict=True):
        assert v[:2] == orig[:2] and len(v) == len(orig)
        compact = v.replace(" ", "")
        assert int("".join(str(int(c, 36)) for c in compact[4:] + compact[:4])) % 97 == 1


def test_dates_of_birth_stay_dates_inside_the_range():
    t = pa.table({"dob": ["1980-05-17", "1991-01-02", "1975-12-30", "2001-07-07"]})
    out = mask_tables({"t": t}).tables["t"]["dob"].to_pylist()
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v) for v in out)
    assert all("1975-12-30" <= v <= "2001-07-07" for v in out)
    assert not set(out) & set(t["dob"].to_pylist())


def test_exclude_and_explicit_types():
    t = pa.table({"email": ["a@x.com", "b@x.com"], "token": ["abc-123", "xyz-456"]})
    out = mask_tables(
        {"t": t}, MaskConfig(exclude_columns=("email",), pii_columns={"token": "ssn"})
    )
    assert out.columns_masked["t"] == ["token"]
    assert out.tables["t"]["email"].equals(t["email"])
    tokens = out.tables["t"]["token"].to_pylist()
    assert all(re.fullmatch(r"(abc|xyz)-\d{3}", v) for v in tokens)
    assert not set(tokens) & {"abc-123", "xyz-456"}
    with pytest.raises(MaskError, match="unknown personal-data type"):
        mask_tables({"t": t}, MaskConfig(pii_columns={"token": "nonsense"}))


def test_impossible_requests_fail_loudly(monkeypatch):
    monkeypatch.setattr(mv, "_pool", lambda name: ["CA"])  # a domain of one value
    with pytest.raises(MaskError, match="cannot find replacements"):
        mask_tables({"t": pa.table({"state": ["CA", "CA"]})})


def test_bare_table_is_refused():
    with pytest.raises(TypeError):
        mask_tables(people())  # type: ignore[arg-type]


def test_transform_apply_rejects_unknown_options():
    with pytest.raises(TypeError):
        Mask().apply({"t": people(10)}, nope=1)
    out = Mask().apply({"t": people(10)}, seed=1)
    assert set(out) == {"t"}


# ---- the command -------------------------------------------------------------------------


def run(capsys, *argv):
    code = main([str(a) for a in argv])
    cap = capsys.readouterr()
    return code, cap.out, cap.err


def test_cli_masks_a_csv_and_leaves_other_columns_as_text(tmp_path, capsys):
    src = tmp_path / "people.csv"
    src.write_text(
        'id,email,amount,note\n1,a@x.com,10.50,hello\n2,,3.10,\n3,c@y.org,007,"x,y"\n',
        encoding="utf-8",
    )
    out = tmp_path / "masked"
    code, text, _ = run(capsys, "mask", src, "-o", out, "--json")
    assert code == 0
    doc = json.loads(text)
    assert doc["columns_masked"] == {"people": ["email"]}
    rows = list(
        pacsv.read_csv(
            out / "people.csv",
            convert_options=pacsv.ConvertOptions(
                column_types={n: pa.string() for n in ("id", "email", "amount", "note")}
            ),
        ).to_pylist()
    )
    assert [r["amount"] for r in rows] == ["10.50", "3.10", "007"]  # not re-typed
    assert rows[1]["email"] in (None, "") and rows[0]["email"] != "a@x.com"
    assert rows[2]["note"] == "x,y"


def test_cli_parquet_and_directory_input(tmp_path, capsys):
    d = tmp_path / "in"
    d.mkdir()
    pq.write_table(people(50), d / "a.parquet")
    pq.write_table(pa.table({"x": [1, 2]}), d / "b.parquet")
    code, text, _ = run(capsys, "mask", d, "-o", tmp_path / "o", "--format", "parquet", "--json")
    assert code == 0
    assert json.loads(text)["columns_masked"]["b"] == []
    assert pq.read_table(tmp_path / "o" / "a.parquet").schema == people(50).schema


def test_cli_refuses_to_overwrite_its_input(tmp_path, capsys):
    src = tmp_path / "p.csv"
    src.write_text("email\na@x.com\n", encoding="utf-8")
    code, _, err = run(capsys, "mask", src, "-o", tmp_path)
    assert code == 2 and "overwrite" in err
    assert src.read_text(encoding="utf-8") == "email\na@x.com\n"


def test_cli_errors_exit_2(tmp_path, capsys):
    code, _, err = run(capsys, "mask", tmp_path / "missing.csv", "-o", tmp_path / "o")
    assert code == 2 and "not found" in err
    src = tmp_path / "p.csv"
    src.write_text("a\n1\n", encoding="utf-8")
    code, _, err = run(capsys, "mask", src, "-o", tmp_path / "o", "--pii", "a")
    assert code == 2 and "COLUMN=TYPE" in err
    code, _, err = run(capsys, "mask", src, "-o", tmp_path / "o", "--pii", "a=bogus")
    assert code == 2 and "unknown personal-data type" in err


def test_cli_exclude_and_pii(tmp_path, capsys):
    src = tmp_path / "p.csv"
    src.write_text("email,token\na@x.com,AB-12\nb@x.com,CD-34\n", encoding="utf-8")
    code, _, _ = run(
        capsys, "mask", src, "-o", tmp_path / "o", "--exclude", "email", "--pii", "token=ssn"
    )
    assert code == 0
    text = (tmp_path / "o" / "p.csv").read_text(encoding="utf-8")
    assert "a@x.com" in text and "AB-12" not in text


def test_empty_and_all_null_columns_are_left_alone():
    t = pa.table({"email": pa.array([None, None], pa.string()), "id": [1, 2]})
    res = mask_tables({"t": t})
    assert res.columns_masked["t"] == []
    assert res.tables["t"].equals(t)
    empty = pa.table({"email": pa.array([], pa.string())})
    assert mask_tables({"t": empty}).tables["t"].num_rows == 0
