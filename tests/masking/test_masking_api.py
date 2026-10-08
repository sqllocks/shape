"""W5-01 (#62): conformance tests for the public masking API (``shape.masking``)."""

from __future__ import annotations

import datetime as dt
import os
import re

import pyarrow as pa
import pytest

from shape import masking
from shape.masking import Masker, MaskingError, MaskingKeyError

# The key file permission check reads POSIX mode bits (Windows has none to read).
posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
KEY = b"0123456789abcdef0123456789abcdef"
OTHER = b"fedcba9876543210fedcba9876543210"
EMAIL = re.compile(r"^[a-z0-9._]+@example\.(com|org|net)$")


# ---- 1. kinds: deterministic, keyed, format-preserving ------------------------------------

SAMPLES = {
    "identifier": ["AB-1234-xy", "100200300", "ZZ99", "a1"],
    "name": ["Alice Johnson", "alice", "SMITH, JOHN", "Bob"],
    "email": ["alice@corp.io", "Bob.Smith@Gmail.com"],
    "phone": ["(555) 201-3344", "+44 20 7946 0958", "555.123.4567"],
    "date": ["2021-03-04", "2021-03-04 10:11:12", "04/03/2021"],
    "text": ["mail alice@corp.io or call (555) 201-3344 from 10.1.2.3, ssn 123-45-6789"],
}


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_same_input_and_key_give_same_output_across_instances(kind):
    for v in SAMPLES[kind]:
        a = Masker(KEY).mask(kind, v)
        b = Masker(KEY).mask(kind, v)
        assert a == b


@pytest.mark.parametrize("kind", sorted(SAMPLES))
def test_other_key_gives_other_output_and_output_differs_from_input(kind):
    for v in SAMPLES[kind]:
        a = Masker(KEY).mask(kind, v)
        assert a != v
        assert Masker(OTHER).mask(kind, v) != a


def test_identifier_keeps_layout_case_and_width():
    m = Masker(KEY)
    out = m.mask("identifier", "AB-1234-xy")
    assert re.fullmatch(r"[A-Z]{2}-\d{4}-[a-z]{2}", out)
    assert m.mask("identifier", 100200300) != 100200300
    n = m.mask("identifier", 100200300)
    assert isinstance(n, int) and len(str(n)) == 9
    assert m.mask("identifier", "5") != "5"


def test_identifier_that_cannot_change_is_an_error():
    with pytest.raises(MaskingError):
        Masker(KEY).mask("identifier", "---")


def test_name_parts_and_style():
    m = Masker(KEY)
    assert m.mask("name", "ALICE").isupper()
    assert m.mask("name", "alice").islower()
    full = m.mask("name", "Alice Johnson")
    assert len(full.split()) == 2
    assert re.fullmatch(r"[^,]+, [^,]+", m.mask("name", "Smith, John"))
    assert " " not in m.mask("name", "Alice Johnson", part="first")
    assert m.mask("name", "Alice", part="first") != "Alice"
    with pytest.raises(ValueError):
        m.mask("name", "Alice", part="middle")


def test_email_is_valid_and_uses_reserved_domains_only():
    m = Masker(KEY)
    seen = set()
    for i in range(300):
        out = m.mask("email", f"user{i}@corp.io")
        assert EMAIL.match(out), out
        seen.add(out.rsplit("@", 1)[1])
    assert seen == {"example.com", "example.org", "example.net"}


def test_email_is_case_insensitive_and_trimmed():
    m = Masker(KEY)
    assert m.mask("email", "Bob.Smith@Gmail.com") == m.mask("email", " bob.smith@gmail.com ")


def test_phone_keeps_punctuation_and_country_code():
    m = Masker(KEY)
    assert re.fullmatch(r"\(\d{3}\) \d{3}-\d{4}", m.mask("phone", "(555) 201-3344"))
    out = m.mask("phone", "+44 20 7946 0958")
    assert out.startswith("+44 ") and re.fullmatch(r"\+44 \d\d \d{4} \d{4}", out)


def test_date_shift_is_bounded_and_keeps_format_and_type():
    m = Masker(KEY)
    d = m.mask("date", dt.date(2021, 3, 4), max_days=30)
    assert isinstance(d, dt.date) and 1 <= abs((d - dt.date(2021, 3, 4)).days) <= 30
    s = m.mask("date", "2021-03-04", max_days=30)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", s)
    t = m.mask("date", "2021-03-04 10:11:12", max_days=30)
    assert t.endswith(" 10:11:12")
    u = m.mask("date", "04/03/2021", max_days=30)
    assert re.fullmatch(r"\d{2}/\d{2}/\d{4}", u)
    dtv = m.mask("date", dt.datetime(2021, 3, 4, 10, 11, 12), max_days=30)
    assert dtv.time() == dt.time(10, 11, 12)


def test_date_subject_shift_preserves_intervals():
    m = Masker(KEY)
    a = m.mask("date", dt.date(2021, 3, 4), subject="patient-7")
    b = m.mask("date", dt.date(2021, 4, 20), subject="patient-7")
    assert (b - a).days == 47
    c = m.mask("date", dt.date(2021, 3, 4), subject="patient-8")
    assert c != a


def test_date_rejects_bad_input():
    with pytest.raises(MaskingError):
        Masker(KEY).mask("date", "not a date")
    with pytest.raises(ValueError):
        Masker(KEY).mask("date", dt.date(2021, 1, 1), max_days=0)


def test_text_masks_embedded_values_consistently_with_columns():
    m = Masker(KEY)
    text = SAMPLES["text"][0]
    out = m.mask("text", text)
    assert "alice@corp.io" not in out and "555) 201-3344" not in out
    assert "10.1.2.3" not in out and "123-45-6789" not in out
    assert m.mask("email", "alice@corp.io") in out
    assert m.mask("phone", "(555) 201-3344") in out
    assert out.startswith("mail ") and " or call " in out


def test_text_without_personal_data_is_unchanged():
    assert Masker(KEY).mask("text", "nothing to see here") == "nothing to see here"


def test_unknown_kind_and_none():
    m = Masker(KEY)
    with pytest.raises(ValueError):
        m.mask("nope", "x")
    assert m.mask("email", None) is None


# ---- 2. referential consistency -----------------------------------------------------------


def test_same_value_same_mask_in_every_table_and_column():
    m = Masker(KEY)
    customers = pa.table({"id": ["C-100", "C-200"], "mail": ["a@x.io", "b@x.io"]})
    orders = pa.table({"cust_ref": ["C-200", "C-100", "C-200"], "n": [1, 2, 3]})
    out = m.mask_tables(
        {"customers": customers, "orders": orders},
        {
            "customers": {"id": "identifier", "mail": "email"},
            "orders": {"cust_ref": "identifier"},
        },
    )
    cid = out["customers"]["id"].to_pylist()
    ref = out["orders"]["cust_ref"].to_pylist()
    assert ref == [cid[1], cid[0], cid[1]]
    assert out["orders"]["n"].to_pylist() == [1, 2, 3]
    assert out["customers"]["mail"].to_pylist() == [
        m.mask("email", "a@x.io"),
        m.mask("email", "b@x.io"),
    ]


def test_batches_do_not_change_results():
    m = Masker(KEY)
    vals = [f"ID-{i:05d}" for i in range(200)]
    whole = m.mask_column("identifier", pa.array(vals))
    parts = pa.chunked_array([pa.array(vals[:70]), pa.array(vals[70:])])
    assert m.mask_column("identifier", parts).to_pylist() == whole.to_pylist()
    assert m.mask_column("identifier", pa.array(vals[::-1])).to_pylist() == whole.to_pylist()[::-1]


def test_column_keeps_nulls_and_types():
    m = Masker(KEY)
    out = m.mask_column("identifier", pa.array([1234, None, 5678], pa.int64()))
    assert out.type == pa.int64() and out[1].as_py() is None
    assert out[0].as_py() != 1234
    dates = m.mask_column("date", pa.array([dt.date(2020, 1, 1), None], pa.date32()), max_days=9)
    assert dates.type == pa.date32() and dates[1].as_py() is None


def test_mask_tables_detects_collisions_instead_of_merging_values():
    m = Masker(KEY)
    # one-digit identifiers: only 9 possible outputs for 12 inputs, so two collide
    t = pa.table({"k": [str(i) for i in range(12)]})
    with pytest.raises(MaskingError):
        m.mask_tables({"t": t}, {"t": {"k": "identifier"}})


# ---- 3. key handling ----------------------------------------------------------------------


def test_short_or_wrong_type_keys_are_refused():
    with pytest.raises(MaskingKeyError):
        Masker(b"short")
    with pytest.raises(MaskingKeyError):
        Masker("")  # type: ignore[arg-type]
    with pytest.raises(MaskingKeyError):
        Masker(None)  # type: ignore[arg-type]
    with pytest.raises(MaskingKeyError):
        Masker(12345)  # type: ignore[arg-type]


def test_generate_key_is_random_and_long_enough():
    a, b = masking.generate_key(), masking.generate_key()
    assert a != b and len(a) >= 32
    Masker(a)


def test_key_never_appears_in_repr_or_errors():
    m = Masker(KEY)
    assert KEY.decode() not in repr(m) and KEY.decode() not in str(m)
    try:
        m.mask("identifier", "---")
    except MaskingError as exc:
        assert KEY.decode() not in str(exc)


def test_load_key_from_file_and_env(tmp_path, monkeypatch):
    f = tmp_path / "k.key"
    f.write_bytes(KEY + b"\n")
    f.chmod(0o600)
    assert masking.load_key(file=f) == KEY
    monkeypatch.setenv("MY_MASK_KEY", KEY.decode())
    assert masking.load_key(env="MY_MASK_KEY") == KEY
    monkeypatch.delenv("MY_MASK_KEY")
    with pytest.raises(MaskingKeyError):
        masking.load_key(env="MY_MASK_KEY")
    with pytest.raises(MaskingKeyError):
        masking.load_key(file=tmp_path / "missing")
    with pytest.raises(MaskingKeyError):
        masking.load_key()
    with pytest.raises(MaskingKeyError):
        masking.load_key(file=f, env="X")


@posix_only
def test_key_file_readable_by_others_is_refused(tmp_path):
    f = tmp_path / "k.key"
    f.write_bytes(KEY)
    f.chmod(0o644)
    with pytest.raises(MaskingKeyError, match="permissions"):
        masking.load_key(file=f)


def test_no_key_bytes_in_masked_output_files(tmp_path):
    m = Masker(KEY)
    out = m.mask_column("email", pa.array(["a@x.io"]))
    assert KEY not in b"".join(str(v).encode() for v in out.to_pylist())
