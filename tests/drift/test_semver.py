"""W1-13: change classes (breaking, additive, cosmetic): the classifier, widening, overrides, the
``shape.diff`` result, ``--fail-on`` and ``--version-from``."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import shape
from shape.drift import semver
from shape.drift.engine import KIND_SEVERITY
from shape.drift.semver import (
    CLASSES,
    DEFAULT_CLASSES,
    classify,
    next_version,
    widening,
)

DOCS = Path(__file__).resolve().parents[2] / "docs"


def change(kind, baseline=None, current=None, **extra):
    return {"column": "c", "kind": kind, "baseline": baseline, "current": current, **extra}


# ---- 1. the classifier -----------------------------------------------------------------------


def test_every_kind_in_the_drift_doc_has_a_default_class():
    text = (DOCS / "DRIFT.md").read_text(encoding="utf-8")
    table = text.split("## What is compared")[1].split("###")[0]
    documented = set()
    for line in table.splitlines():
        if line.startswith("|"):
            documented |= set(re.findall(r"`([a-z_]+)`", line.split("|")[1]))
    documented -= {""}
    assert "column_added" in documented and "reference_match_change" in documented
    assert documented <= set(DEFAULT_CLASSES), documented - set(DEFAULT_CLASSES)


def test_every_engine_kind_has_a_default_class_and_nothing_else_does():
    # a kind added to the engine without a class fails here
    assert set(KIND_SEVERITY) == set(DEFAULT_CLASSES)
    assert set(DEFAULT_CLASSES.values()) <= set(CLASSES)


@pytest.mark.parametrize(
    "kind",
    [
        "table_removed",
        "column_removed",
        "dtype_change",
        "pattern_change",
        "dependency_broken",
        "reference_match_change",
    ],
)
def test_breaking_kinds(kind):
    got = classify(change(kind, "a", "b"))
    assert got.class_ == "breaking" and got.reason


@pytest.mark.parametrize("kind", ["table_added", "column_added", "new_categorical_values"])
def test_additive_kinds(kind):
    assert classify(change(kind)).class_ == "additive"


@pytest.mark.parametrize(
    "kind",
    sorted(
        set(KIND_SEVERITY)
        - {
            "table_removed",
            "column_removed",
            "dtype_change",
            "pattern_change",
            "dependency_broken",
            "reference_match_change",
            "table_added",
            "column_added",
            "new_categorical_values",
            "null_rate_change",
            "uniqueness_change",
        }
    ),
)
def test_every_other_kind_is_cosmetic(kind):
    assert classify(change(kind, 1, 2)).class_ == "cosmetic"


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (0, 0.001, "breaking"),  # boundary: the smallest rise from zero
        (0.0, 0.5, "breaking"),
        (0.02, 0.5, "cosmetic"),
        (0.5, 0.0, "cosmetic"),  # nulls going away never breaks a reader
        (0, 0, "cosmetic"),  # no longer a rise from zero
    ],
)
def test_null_rate_change(before, after, expected):
    assert classify(change("null_rate_change", before, after)).class_ == expected


@pytest.mark.parametrize(
    ("before", "after", "extra", "expected"),
    [
        (1.0, 0.9, {}, "breaking"),  # as many distinct values as rows, now duplicates
        (0.97, 0.80, {"detail": {"baseline_primary_key": True}}, "breaking"),  # a primary key
        (0.97, 0.80, {}, "cosmetic"),  # was not unique
        (0.80, 0.97, {}, "cosmetic"),  # became more unique
        (1.0, 1.0, {}, "cosmetic"),  # still unique
    ],
)
def test_uniqueness_change(before, after, extra, expected):
    assert classify(change("uniqueness_change", before, after, **extra)).class_ == expected


def test_unknown_kind_is_a_value_error():
    with pytest.raises(ValueError, match="made_up"):
        classify(change("made_up"))


def test_classification_serialises():
    got = classify(change("column_removed", "present", "absent"))
    assert got.to_dict() == {"class": "breaking", "reason": got.reason}
    assert "removed" in got.reason and "\n" not in got.reason


# ---- 2. widening -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("int8", "int16"),
        ("int32", "int64"),
        ("uint8", "uint16"),
        ("uint32", "uint64"),
        ("uint32", "int64"),  # unsigned into a wider signed integer
        ("float32", "float64"),
        ("int32", "float64"),
        ("int8", "float64"),
        ("uint16", "float64"),
        ("string", "large_string"),
        ("str", "large_string"),
        ("binary", "large_binary"),
        ("decimal128(10, 2)", "decimal128(12, 2)"),
        ("decimal128(10, 2)", "decimal256(40, 2)"),
    ],
)
def test_widening_pairs(before, after):
    assert widening(before, after) is True


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("int64", "int32"),  # narrowing
        ("int32", "int32"),  # same
        ("int32", "uint64"),  # signed into unsigned
        ("uint32", "int32"),  # same width, different sign
        ("int64", "float64"),  # a 64-bit integer does not fit a float64 exactly
        ("int32", "float32"),
        ("float64", "float32"),
        ("float64", "int64"),
        ("large_string", "string"),
        ("string", "binary"),
        ("binary", "large_string"),
        ("decimal128(12, 2)", "decimal128(10, 2)"),
        ("decimal128(10, 2)", "decimal128(12, 3)"),  # a different scale
        ("string", "int64"),
        ("integer", "float"),  # the profile's families carry no width
        (None, "int64"),
    ],
)
def test_not_widening_pairs(before, after):
    assert widening(before, after) is False


def test_widening_dtype_change_stays_breaking_by_default():
    got = classify(change("dtype_change", "int32", "int64", detail={"widening": True}))
    assert got.class_ == "breaking" and "widening" in got.reason


def test_widening_is_detected_from_the_type_names_without_detail():
    assert classify(change("dtype_change", "int32", "int64"), {"dtype_widening": "additive"}) == (
        classify(
            change("dtype_change", "int32", "int64", detail={"widening": True}),
            {"dtype_widening": "additive"},
        )
    )


def test_dtype_widening_class_applies_only_to_widening():
    classes = {"dtype_widening": "additive"}
    assert classify(change("dtype_change", "int32", "int64"), classes).class_ == "additive"
    assert classify(change("dtype_change", "int64", "int32"), classes).class_ == "breaking"
    assert classify(change("dtype_change", "int64", "int32")).class_ == "breaking"
    assert classify(change("dtype_change", "integer", "string"), classes).class_ == "breaking"


def test_dtype_change_class_applies_to_what_is_not_widening():
    classes = {"dtype_change": "cosmetic", "dtype_widening": "additive"}
    assert classify(change("dtype_change", "int32", "int64"), classes).class_ == "additive"
    assert classify(change("dtype_change", "int64", "int32"), classes).class_ == "cosmetic"


# ---- 3. overrides: classes ---------------------------------------------------------------------


def test_a_class_override_replaces_the_default_and_says_so():
    got = classify(change("mean_shift", 1, 2), {"mean_shift": "breaking"})
    assert got.class_ == "breaking" and "policy" in got.reason
    got = classify(change("column_removed"), {"column_removed": "cosmetic"})
    assert got.class_ == "cosmetic"
    # an override beats the conditional rule, in both branches
    assert classify(
        change("null_rate_change", 0, 0.3), {"null_rate_change": "cosmetic"}
    ).class_ == ("cosmetic")
    assert classify(
        change("null_rate_change", 0.1, 0.3), {"null_rate_change": "breaking"}
    ).class_ == ("breaking")


def test_unknown_kind_or_class_in_overrides_is_a_value_error():
    with pytest.raises(ValueError, match="made_up"):
        classify(change("mean_shift"), {"made_up": "breaking"})
    with pytest.raises(ValueError, match="major"):
        classify(change("mean_shift"), {"mean_shift": "major"})
    with pytest.raises(ValueError, match="classes"):
        semver.check_classes(["column_added"])
    with pytest.raises(ValueError, match="severity"):
        semver.check_classes({"column_added": "high"})


def test_pseudo_kind_is_accepted_but_is_not_a_kind():
    semver.check_classes({"dtype_widening": "additive", "column_added": "cosmetic"})
    assert "dtype_widening" not in DEFAULT_CLASSES
    with pytest.raises(ValueError, match="dtype_widening"):
        classify(change("dtype_widening"))


# ---- bump and version ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("counts", "bump"),
    [
        ((1, 0, 0), "major"),
        ((1, 5, 5), "major"),
        ((0, 1, 0), "minor"),
        ((0, 2, 9), "minor"),
        ((0, 0, 1), "patch"),
        ((0, 0, 0), "none"),
    ],
)
def test_bump_rule(counts, bump):
    assert semver.bump_of(*counts) == bump


@pytest.mark.parametrize(
    ("bump", "expected"),
    [("major", "2.0.0"), ("minor", "1.5.0"), ("patch", "1.4.3"), ("none", "1.4.2")],
)
def test_next_version(bump, expected):
    assert next_version("1.4.2", bump) == expected


@pytest.mark.parametrize("bad", ["1.4", "1.4.2.1", "v1.4.2", "1.4.x", "", "1.4.2-rc1", "-1.0.0"])
def test_next_version_rejects_anything_but_x_y_z(bad):
    with pytest.raises(ValueError, match="X.Y.Z"):
        next_version(bad, "minor")


def test_summarise_counts_unplanned_and_planned_separately():
    changes = [
        {"class": "breaking"},
        {"class": "additive"},
        {"class": "cosmetic"},
        {"class": "cosmetic"},
        {"class": "breaking", "planned": {"id": "p", "action": "expect"}},
    ]
    s = semver.summarise(changes, [True, True, True, True, False], planned=True)
    assert s == {
        "bump": "major",
        "breaking": 1,
        "additive": 1,
        "cosmetic": 2,
        "planned": {"breaking": 1, "additive": 0, "cosmetic": 0},
    }
    only_planned = semver.summarise(changes[4:], [False], planned=True)
    assert only_planned["bump"] == "none" and only_planned["planned"]["breaking"] == 1
    assert "planned" not in semver.summarise(changes[:1])


def test_fails_orders_the_classes():
    changes = [{"class": "cosmetic"}]
    assert semver.fails(changes, None, "cosmetic") is True
    assert semver.fails(changes, None, "additive") is False
    assert semver.fails(changes, None, "breaking") is False
    changes = [{"class": "additive"}]
    assert [semver.fails(changes, None, c) for c in CLASSES] == [False, True, True]
    assert semver.fails([], None, "cosmetic") is False
    assert semver.fails([{"class": "breaking"}], [False], "cosmetic") is False  # not counted
    with pytest.raises(ValueError, match="fail_on"):
        semver.fails(changes, None, "high")


# ---- 4. and 5. the result of shape.diff --------------------------------------------------------


def table(n=200, *, nulls=0, extra=False, drop=False, shift=0.0, wide=False):
    import pyarrow as pa

    cols = {
        "id": list(range(n)),
        "status": [["new", "paid", "paid", "shipped"][i % 4] for i in range(n)],
        "note": [None if i < nulls else f"note {i}" for i in range(n)],
        "amount": [10.0 + ((i * 37) % 200) / 10 + shift for i in range(n)],
    }
    if extra:
        cols["tier"] = [["gold", "silver"][i % 2] for i in range(n)]
    if drop:
        del cols["status"]
    return pa.table(cols)


def kinds(result):
    return {(c["column"], c["kind"]): c for c in result.changes}


def test_every_change_carries_class_and_reason_and_the_result_a_summary():
    base = shape.profile(table())
    r = shape.diff(base, shape.profile(table(drop=True)))
    c = kinds(r)[("status", "column_removed")]
    assert c["class"] == "breaking" and c["class_reason"]
    assert r.to_dict(semver=True)["semver"] == r.semver
    assert "semver" not in r.to_dict()  # the shipped to_dict contract is unchanged
    assert r.semver["bump"] == "major" and r.semver["breaking"] == 1
    assert "planned" not in r.semver
    assert all(x["class"] in CLASSES for x in r.changes)


def test_an_added_column_alone_is_minor():
    base = shape.profile(table())
    r = shape.diff(base, shape.profile(table(extra=True)))
    assert [(c["kind"], c["class"]) for c in r.changes] == [("column_added", "additive")]
    assert r.semver == {"bump": "minor", "breaking": 0, "additive": 1, "cosmetic": 0}


def test_a_mean_shift_alone_is_patch():
    base = shape.profile(table())
    r = shape.diff(base, shape.profile(table(shift=40.0)))
    assert {c["kind"] for c in r.changes} >= {"mean_shift"}
    assert r.semver["bump"] == "patch" and r.semver["breaking"] == 0
    assert all(c["class"] == "cosmetic" for c in r.changes)


def test_no_change_is_bump_none():
    base = shape.profile(table())
    r = shape.diff(base, shape.profile(table()))
    assert r.changes == [] and r.semver == {
        "bump": "none",
        "breaking": 0,
        "additive": 0,
        "cosmetic": 0,
    }
    assert r.failed is False


def test_null_rate_rising_from_zero_is_breaking_and_from_a_little_is_cosmetic():
    zero = shape.profile(table())
    some = shape.profile(table(nulls=4))  # 2% nulls
    r = shape.diff(zero, shape.profile(table(nulls=60)))
    assert kinds(r)[("note", "null_rate_change")]["class"] == "breaking"
    assert r.semver["bump"] == "major"
    r = shape.diff(some, shape.profile(table(nulls=60)))
    assert kinds(r)[("note", "null_rate_change")]["class"] == "cosmetic"
    assert r.semver["bump"] == "patch"


def test_class_counts_add_up_to_the_changes():
    r = shape.diff(shape.profile(table()), shape.profile(table(extra=True, drop=True, nulls=60)))
    s = r.semver
    assert s["breaking"] + s["additive"] + s["cosmetic"] == len(r.changes)


def test_policy_classes_and_column_classes_change_the_class():
    base, cur = shape.profile(table()), shape.profile(table(extra=True))
    r = shape.diff(base, cur, policy={"classes": {"column_added": "cosmetic"}})
    assert r.changes[0]["class"] == "cosmetic" and "policy" in r.changes[0]["class_reason"]
    assert r.semver["bump"] == "patch"
    # a column pattern beats the policy-wide class; the most specific pattern wins
    policy = {
        "classes": {"column_added": "breaking"},
        "column_classes": {
            "t*": {"column_added": "cosmetic"},
            "tier": {"column_added": "additive"},
        },
    }
    r = shape.diff(base, cur, policy=policy)
    assert r.changes[0]["class"] == "additive"
    r = shape.diff(
        base, cur, policy={**policy, "column_classes": {"t*": {"column_added": "cosmetic"}}}
    )
    assert r.changes[0]["class"] == "cosmetic"
    r = shape.diff(base, cur, policy={"column_classes": {"other": {"column_added": "breaking"}}})
    assert r.changes[0]["class"] == "additive"  # the pattern does not match


def test_policy_class_errors_are_value_errors():
    base, cur = shape.profile(table()), shape.profile(table(extra=True))
    for bad in (
        {"classes": {"made_up": "breaking"}},
        {"classes": {"column_added": "major"}},
        {"classes": ["column_added"]},
        {"column_classes": {"tier": {"made_up": "breaking"}}},
        {"column_classes": {"tier": {"column_added": "high"}}},
        {"column_classes": {"tier": "breaking"}},
    ):
        with pytest.raises(ValueError):
            shape.diff(base, cur, policy=bad)


def test_policy_file_with_classes(tmp_path):
    import json

    p = tmp_path / "policy.json"
    p.write_text(json.dumps({"classes": {"column_added": "breaking"}}), encoding="utf-8")
    r = shape.diff(shape.profile(table()), shape.profile(table(extra=True)), policy=str(p))
    assert r.changes[0]["class"] == "breaking"
    # a contract's "drift" object carries the same keys
    p.write_text(json.dumps({"drift": {"classes": {"column_added": "cosmetic"}}}), encoding="utf-8")
    r = shape.diff(shape.profile(table()), shape.profile(table(extra=True)), policy=str(p))
    assert r.changes[0]["class"] == "cosmetic"


def test_fail_on_sets_failed_and_orders_the_classes():
    base = shape.profile(table())
    added = shape.profile(table(extra=True))
    removed = shape.profile(table(drop=True))
    shifted = shape.profile(table(shift=40.0))
    for cur, expected in (
        (removed, {"breaking": True, "additive": True, "cosmetic": True}),
        (added, {"breaking": False, "additive": True, "cosmetic": True}),
        (shifted, {"breaking": False, "additive": False, "cosmetic": True}),
        (shape.profile(table()), {"breaking": False, "additive": False, "cosmetic": False}),
    ):
        for level, want in expected.items():
            r = shape.diff(base, cur, fail_on=level)
            assert r.failed is want, (level, want)
            assert r.to_dict()["failed"] is want and r.to_dict()["fail_on"] == level
    assert shape.diff(base, removed).failed is False  # no fail_on: never failed
    assert "failed" not in shape.diff(base, removed).to_dict()


def test_fail_on_rejects_an_unknown_class():
    base = shape.profile(table())
    with pytest.raises(ValueError, match="fail_on"):
        shape.diff(base, base, fail_on="major")


def test_fail_on_respects_min_severity_and_the_ignore_list():
    base = shape.profile(table())
    removed = shape.profile(table(drop=True))
    assert shape.diff(base, removed, fail_on="breaking", ignore_columns=["status"]).failed is False
    # a cosmetic change below min_severity is not reported, so it cannot fail the run
    shifted = shape.profile(table(shift=40.0))
    r = shape.diff(base, shifted, fail_on="cosmetic", thresholds={"min_severity": "high"})
    assert r.changes == [] and r.failed is False
