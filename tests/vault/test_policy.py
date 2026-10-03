"""Item 3: the per-column policy file."""

from __future__ import annotations

import json

import pytest

from shape import compat
from shape.vault.errors import VaultInputError
from shape.vault.policy import VaultPolicy, load_policy, parse_policy


def _doc(**kw):
    return {"format": "shape-vault-policy", "version": 1, **kw}


def test_precedence_explicit_then_classification_then_default():
    p = parse_policy(
        _doc(
            default="none",
            by_classification={"CONFIDENTIAL": "categories", "SECRET": "all"},
            columns={"t.a": "extremes", "t.b": "none"},
        )
    )
    assert p.policy_for("t.a", "CONFIDENTIAL") == "extremes"  # explicit wins
    assert p.policy_for("t.b", "SECRET") == "none"  # explicit none still wins
    assert p.policy_for("t.c", "CONFIDENTIAL") == "categories"
    assert p.policy_for("t.c", "SECRET") == "all"
    assert p.policy_for("t.c", "PUBLIC") == "none"  # default
    assert p.policy_for("t.c", None) == "none"
    assert p.policy_for("t.c", "no-such-level") == "none"


def test_alias_classification_stands_for_its_level():
    p = parse_policy(_doc(by_classification={"PII": "categories"}))
    assert p.policy_for("t.c", "CONFIDENTIAL") == "categories"
    assert p.policy_for("t.c", "pii") == "categories"
    assert p.policy_for("t.c", "SENSITIVE") == "categories"


def test_default_applies_when_nothing_else_does():
    assert parse_policy(_doc(default="all")).policy_for("t.c") == "all"
    assert parse_policy(_doc()).policy_for("t.c") == "none"


@pytest.mark.parametrize("value", ["everything", "ALL", "", 3, None, ["all"]])
def test_unknown_policy_value_is_input_error(value):
    with pytest.raises(VaultInputError, match="unknown policy"):
        parse_policy(_doc(columns={"t.a": value}))
    with pytest.raises(VaultInputError, match="unknown policy"):
        parse_policy(_doc(default=value))


def test_unknown_classification_is_input_error_naming_it():
    with pytest.raises(VaultInputError, match="COSMIC"):
        parse_policy(_doc(by_classification={"COSMIC": "all"}))


def test_column_not_in_profile_is_input_error_naming_it():
    p = parse_policy(_doc(columns={"t.a": "all", "t.ghost": "all"}))
    with pytest.raises(VaultInputError, match=r"t\.ghost"):
        p.validate_against({"t.a", "t.b"})
    p.validate_against({"t.a", "t.ghost"})


def test_column_names_are_table_dot_column():
    with pytest.raises(VaultInputError, match="TABLE.COLUMN"):
        parse_policy(_doc(columns={"nodot": "all"}))


@pytest.mark.parametrize("bad", [[], "x", 3, None])
def test_policy_must_be_an_object(bad):
    with pytest.raises(VaultInputError):
        parse_policy(bad)


def test_format_is_required_and_checked():
    with pytest.raises(VaultInputError):
        parse_policy({"version": 1})
    with pytest.raises(VaultInputError):
        parse_policy({"format": "shape-vault", "version": 1})


def test_newer_version_names_the_minimum_release():
    doc = {"format": "shape-vault-policy", "version": 2, "min_shape_version": "9.9.0"}
    with pytest.raises(VaultInputError, match="9.9.0"):
        parse_policy(doc)


def test_round_trip_declares_format_and_version():
    p = VaultPolicy("all", {"CONFIDENTIAL": "categories"}, {"t.a": "none"})
    d = p.to_dict()
    assert d["format"] == "shape-vault-policy" and d["version"] == 1
    assert parse_policy(d) == p
    assert compat.KINDS["vault-policy"].current == 1


def test_load_policy_file_errors_name_the_file(tmp_path):
    bad = tmp_path / "p.json"
    bad.write_text("{not json")
    with pytest.raises(VaultInputError, match="p.json"):
        load_policy(bad)
    good = tmp_path / "ok.json"
    good.write_text(json.dumps(_doc(default="all")))
    assert load_policy(good).default == "all"
