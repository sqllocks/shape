"""Regressions found by the second privacy bug hunt (HUNT2-privacy)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import shape
from shape.privacy.cli import main as privacy_main
from shape.privacy.safe_profile import ColumnConfig, SafeConfig, SafeProfile, to_safe_profile


@pytest.fixture
def rare_profile(tmp_path: Path) -> Path:
    csv = tmp_path / "d.csv"
    rows = ["city"] + ["Austin"] * 60 + ["Boston"] * 30 + ["Zed", "Yak"]
    csv.write_text("\n".join(rows) + "\n", encoding="utf-8")
    out = tmp_path / "d.shape"
    shape.save(shape.profile(str(csv), name="d"), str(out))
    return out


# --- #596: a minimum cohort below 2 is not a cohort -------------------------------------------


@pytest.mark.parametrize("k", [1, 0, -3, True])
def test_a_minimum_cohort_below_two_is_refused(k):
    with pytest.raises(ValueError, match="at least 2"):
        SafeConfig(k=k)
    with pytest.raises(ValueError, match="at least 2"):
        ColumnConfig(k=k)


def test_the_smallest_cohort_is_two_and_default_still_works():
    assert SafeConfig(k=2).column_k(None) == 2
    assert ColumnConfig(k=2).k == 2
    assert SafeConfig().column_k(None) == 5
    assert SafeConfig(unsafe_full_fidelity=True).column_k("x") == 1  # the one explicit opt-out


@pytest.mark.parametrize(
    "flag", [["--k", "1"], ["--k", "0"], ["--k", "-3"], ["--column-k", "city=0"]]
)
def test_the_safe_command_refuses_a_cohort_below_two(rare_profile, tmp_path, flag, capsys):
    out = tmp_path / "s.json"
    assert privacy_main(["safe", str(rare_profile), "-o", str(out), *flag]) == 2
    assert "at least 2" in capsys.readouterr().err
    assert not out.exists()


def test_a_cohort_of_two_still_folds_one_row_categories(rare_profile, tmp_path):
    out = tmp_path / "s.json"
    assert privacy_main(["safe", str(rare_profile), "-o", str(out), "--k", "2"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "Zed" not in text and "Yak" not in text
    doc = json.loads(text)
    assert doc["unsafe"] is False
    assert to_safe_profile(str(rare_profile), SafeConfig(k=2)).unsafe is False


# --- #600, #601: masking edge cases -----------------------------------------------------------

import datetime as dt  # noqa: E402

import pyarrow as pa  # noqa: E402

from shape.masking import Masker, MaskingError  # noqa: E402

_KEYS = [bytes([i]) * 32 for i in range(1, 9)]


@pytest.mark.parametrize(
    "value", [dt.date(1, 1, 2), dt.date(9999, 12, 31), dt.datetime(9999, 12, 31, 23)]
)
def test_a_date_at_the_edge_of_the_calendar_is_a_masking_error_or_a_valid_date(value):
    # whatever the key, the result is a date or a MaskingError: never an OverflowError
    for key in _KEYS:
        try:
            out = Masker(key).mask("date", value, max_days=400)
        except MaskingError as e:
            assert "range" in str(e)
        else:
            assert type(out) is type(value)


def test_the_error_reaches_mask_column_as_a_masking_error():
    errors = 0
    for key in _KEYS:
        try:
            Masker(key).mask_column("date", pa.array([dt.date(1, 1, 2)], pa.date32()), max_days=400)
        except MaskingError:
            errors += 1
    assert errors, "the sign of the shift depends on the key, so some key must reach the edge"


def test_a_date_in_the_middle_of_the_calendar_is_unchanged_by_the_fix():
    out = Masker(b"k" * 32).mask("date", "2024-01-15")
    assert out == "2024-03-08"


def test_emails_that_differ_by_case_or_space_are_one_address():
    m = Masker(b"k" * 32)
    table = pa.table({"e": ["Bob@x.com", "bob@x.com", " BOB@X.COM ", "al@x.com"]})
    out = m.mask_tables({"t": table}, {"t": {"e": "email"}})["t"].column("e").to_pylist()
    assert out[0] == out[1] == out[2]
    assert out[3] != out[0]


def test_two_different_emails_that_collide_are_still_an_error(monkeypatch):
    m = Masker(b"k" * 32)
    monkeypatch.setattr(Masker, "_mask_email", lambda self, value: "same@example.com")
    table = pa.table({"e": ["a@x.com", "b@x.com"]})
    with pytest.raises(MaskingError, match="same mask"):
        m.mask_tables({"t": table}, {"t": {"e": "email"}})


# --- #650: release_for and the joint block ----------------------------------------------------

from shape.privacy.policy import VALUE_KEYS, release_for  # noqa: E402


def _joint_doc() -> dict:
    cond = {
        "given": "city",
        "target": "state",
        "cramers_v": 1.0,
        "table": {
            "SECRETCITYA": {"n": 136, "p": {"SECRETSTATE1": 1.0}},
            "RARETOWN": {"n": 2, "p": {"RAREPLACE": 1.0}},
            "MIXED": {"n": 100, "p": {"BIGSTATE": 0.98, "TINYSTATE": 0.02}},
        },
    }
    dep = {
        "determinant": ["city"],
        "dependent": "state",
        "confidence": 0.9,
        "violations": [
            {
                "determinant_value": "SECRETCITYA",
                "rows": 50,
                "distinct_dependents": 2,
                "dependent_values": {"SECRETSTATE1": 40, "VIOLATIONSTATE": 10},
            },
            {
                "determinant_value": "RARETOWN",
                "rows": 3,
                "distinct_dependents": 2,
                "dependent_values": {"RARESTATE": 2, "RAREOTHER": 1},
            },
        ],
    }
    col = {"kind": "text", "count": 400, "classification": "PUBLIC"}
    return {
        "rows": 400,
        "columns": {
            "city": {**col, "placeholders": [{"value": "00000", "kind": "listed", "count": 40}]},
            "state": dict(col),
            "zip": dict(col),
        },
        "joint": {
            "version": 1,
            "columns": ["city", "state"],
            "associations": [{"a": "city", "b": "state", "cramers_v": 1.0}],
            "dependencies": [dep],
            "conditionals": [cond],
        },
    }


def test_values_of_a_column_above_the_target_leave_the_joint_block():
    r = release_for(_joint_doc(), {"city": "CONFIDENTIAL"}, "PUBLIC")
    text = json.dumps(r.shape)
    for leaked in ("SECRET", "RARE", "VIOLATIONSTATE", "00000"):
        assert leaked not in text, leaked
    assert "placeholders" in VALUE_KEYS
    assert r.shape["joint"]["associations"] == [{"a": "city", "b": "state", "cramers_v": 1.0}]


def test_cells_below_the_minimum_cohort_are_withheld_from_the_joint_block():
    r = release_for(_joint_doc(), {}, "PUBLIC", minimum_cohort=5)
    text = json.dumps(r.shape["joint"])
    for small in ("RARETOWN", "RAREPLACE", "TINYSTATE", "RARESTATE", "RAREOTHER"):
        assert small not in text, small
    # cells at or above the minimum are kept, so the block is still useful
    (cond,) = r.shape["joint"]["conditionals"]
    assert set(cond["table"]) == {"SECRETCITYA", "MIXED"}
    assert cond["table"]["MIXED"]["p"] == {"BIGSTATE": 0.98}
    (dep,) = r.shape["joint"]["dependencies"]
    assert [v["determinant_value"] for v in dep["violations"]] == ["SECRETCITYA"]
    assert dep["violations"][0]["dependent_values"] == {"SECRETSTATE1": 40, "VIOLATIONSTATE": 10}


def test_a_document_without_a_joint_block_is_released_as_before():
    doc = _joint_doc()
    del doc["joint"]
    assert "joint" not in release_for(doc, {}, "PUBLIC").shape


# --- #657: the leak scanner and format versions -----------------------------------------------

from shape.privacy.safe_validator import SafeProfileValidator  # noqa: E402


def _safe_doc(**patch) -> dict:
    doc = {
        "format": "shape-safe-profile",
        "version": 1,
        "schema_version": 1,
        "redaction_manifest": {},
        "tables": {"t": {"row_count": 10, "columns": {}}},
    }
    doc.update(patch)
    return doc


def _rules(doc) -> set[str]:
    return {f.rule for f in SafeProfileValidator().validate_data(doc).findings}


def test_a_current_safe_profile_is_still_clean():
    assert _rules(_safe_doc()) == set()
    legacy = _safe_doc()
    del legacy["format"], legacy["version"]  # a profile of an older writer declares less
    assert _rules(legacy) == set()


def test_a_newer_version_is_a_finding_that_names_the_release():
    result = SafeProfileValidator().validate_data(
        _safe_doc(version=99, schema_version=99, min_shape_version="9.9.9")
    )
    (finding,) = result.findings
    assert finding.rule == "unsupported-version"
    assert "version 99" in finding.detail and "9.9.9" in finding.detail


@pytest.mark.parametrize(
    "patch",
    [
        {"schema_version": "1"},
        {"version": True},
        {"schema_version": 0},
        {"version": 2.0},
        {"version": 1, "schema_version": "x"},
    ],
)
def test_a_malformed_version_is_a_finding(patch):
    assert _rules(_safe_doc(**patch)) == {"format-version"}


def test_another_format_is_a_finding():
    assert _rules(_safe_doc(format="other")) == {"format-version"}
    assert _rules(_safe_doc(format=["x"])) == {"format-version"}


# --- #669: the profile registry and the unsafe stamp ------------------------------------------

from shape.registry.profiles import ProfileRegistry, ProfileRegistryError  # noqa: E402


def test_the_profile_registry_refuses_an_unsafe_full_fidelity_save(rare_profile, tmp_path):
    reg = ProfileRegistry(tmp_path / "reg")
    profile = shape.load(str(rare_profile))
    with pytest.raises(ProfileRegistryError, match="unsafe"):
        reg.save(
            profile,
            system="s",
            name="n",
            safe=True,
            safe_config=SafeConfig(unsafe_full_fidelity=True),
        )
    assert not list((tmp_path / "reg").rglob("*.safe.json"))


def test_the_profile_registry_still_saves_the_safe_form(rare_profile, tmp_path):
    reg = ProfileRegistry(tmp_path / "reg")
    ids = reg.save(shape.load(str(rare_profile)), system="s", name="n", safe=True)
    assert ids == ["s/d/n"]
    (path,) = (tmp_path / "reg").rglob("*.safe.json")
    text = path.read_text(encoding="utf-8")
    assert json.loads(text)["unsafe"] is False and "Zed" not in text
    assert reg.validate() == []


# --- #671: registry ref, tag and metadata failure paths ---------------------------------------

import datetime  # noqa: E402

from shape.registry.local import LocalRegistry, RegistryError  # noqa: E402


@pytest.fixture
def registry(tmp_path: Path) -> LocalRegistry:
    r = LocalRegistry(tmp_path / "reg")
    r.commit("n", b"content")
    return r


@pytest.mark.parametrize("damage", ["", "   \n", "not-a-hash", "../../layout.json", "A" * 64])
@pytest.mark.parametrize("where", ["refs", "tags"])
def test_a_damaged_ref_or_tag_is_a_registry_error(registry, damage, where):
    directory = registry.root / where / "n"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "bad").write_text(damage, encoding="utf-8")
    with pytest.raises(RegistryError, match="damaged"):
        registry.checkout("n", "bad")
    with pytest.raises(RegistryError, match="damaged"):
        registry.resolve("n", "bad")


def test_good_refs_tags_and_hashes_still_resolve(registry):
    h = registry.resolve("n")
    registry.tag("n", "v1")
    registry.promote("n", "latest", "prod")
    assert registry.checkout("n", "v1") == registry.checkout("n", "prod") == b"content"
    assert registry.resolve("n", h) == h


def test_a_metadata_value_that_is_not_json_writes_nothing(registry):
    before = sorted(p.name for p in (registry.root / "objects").iterdir())
    log_before = registry.log("n")
    with pytest.raises(RegistryError, match="metadata"):
        registry.commit("n", b"zzz", {"d": datetime.datetime.now()})
    assert sorted(p.name for p in (registry.root / "objects").iterdir()) == before
    assert registry.log("n") == log_before
    assert registry.resolve("n") == log_before[-1]["content_id"]


def test_tag_replaces_the_file_atomically(registry, monkeypatch):
    registry.tag("n", "v1")
    seen = []
    real = Path.write_text

    def spy(self, *a, **k):
        seen.append(self.name)
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "write_text", spy)
    registry.tag("n", "v1")
    assert "v1" not in seen, "tag() truncates the tag file in place"
    assert not [p for p in (registry.root / "tags" / "n").iterdir() if p.name.startswith(".tmp-")]


# --- #679: wrongly typed documents are a ValueError -------------------------------------------

from shape.privacy.release import redact_sensitive, suppress_shape  # noqa: E402

_BAD_COLUMNS = ["oops", 7, [1], None, {"a": 5}, {"a": "x"}, {"a": [1]}]


@pytest.mark.parametrize("columns", _BAD_COLUMNS)
@pytest.mark.parametrize(
    "call",
    [
        lambda d: suppress_shape(d),
        lambda d: redact_sensitive(d, {}),
        lambda d: release_for(d, {}),
    ],
    ids=["suppress_shape", "redact_sensitive", "release_for"],
)
def test_a_wrongly_typed_columns_field_is_a_value_error(call, columns):
    with pytest.raises(ValueError, match="columns"):
        call({"rows": 10, "columns": columns})


@pytest.mark.parametrize(
    "table",
    [
        {"name": "t", "row_count": 3, "columns": "oops"},
        {"name": "t", "row_count": 3, "columns": {"a": 5}},
        {"name": "t", "row_count": 3, "primary_key": 5},
        {"name": "t", "row_count": 3, "detected_fks": [1]},
        {"row_count": 3},
        5,
    ],
)
def test_a_malformed_safe_profile_table_is_a_value_error(table):
    with pytest.raises(ValueError, match="safe profile"):
        SafeProfile.from_dict({"tables": {"t": table}})


def test_well_formed_documents_are_unchanged_by_the_type_checks():
    doc = {"rows": 10, "columns": {"a": {"kind": "numeric", "count": 10}}}
    assert release_for(doc, {}).shape["columns"]["a"]["count"] == 10
    assert suppress_shape(doc)["columns"]["a"]["count"] == 10
    assert redact_sensitive(doc, {})["columns"]["a"]["count"] == 10
    assert SafeProfile.from_dict({"tables": {}}).tables == {}
