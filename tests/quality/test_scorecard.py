"""Data quality scorecards by dimension (W3-06)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import pyarrow as pa
import pytest

from shape.quality import GateRunner, VerifyRunner, load_gate_schema
from shape.quality.scorecard import (
    DIMENSIONS,
    GATE_DIMENSION,
    ScorecardError,
    SuppressionError,
    build_scorecard,
    column_owners,
    load_suppressions,
    record_scorecard,
    save_suppressions,
    scorecard_trend,
)
from shape.registry.local import LocalRegistry

FIXTURES = Path(__file__).parent / "fixtures"
GATES = json.loads((FIXTURES / "gates.json").read_text())


@pytest.fixture
def gates(tmp_path):
    p = tmp_path / "gates.json"
    p.write_text(json.dumps(GATES))
    return load_gate_schema(p)


def tables(bad=True):
    if bad:
        return {
            "customer": pa.table(
                {"id": [1, 2, 2, 4], "name": ["a", None, "c", "d"], "age": [30, 200, 40, None]}
            ),
            "order": pa.table({"id": [1, 2, 3, 4], "customer_id": [1, 9, 2, 4]}),
        }
    return {
        "customer": pa.table({"id": [1, 2], "name": ["a", "b"], "age": [30, 40]}),
        "order": pa.table({"id": [1, 2], "customer_id": [1, 2]}),
    }


def run(gates, t, config=None):
    from shape.quality import VerifyConfig

    cfg = VerifyConfig.from_dict(config) if config else None
    return VerifyRunner(gates, config=cfg).run(t)


# -- the mapping from checks to dimensions ----------------------------------------------------


def test_six_standard_dimension_names():
    assert DIMENSIONS == (
        "accuracy",
        "completeness",
        "conformity",
        "consistency",
        "timeliness",
        "uniqueness",
    )


def test_every_registered_gate_maps_to_one_dimension():
    for gate in GateRunner.available_gates():
        assert GATE_DIMENSION[gate] in DIMENSIONS, gate
    assert set(GATE_DIMENSION) == set(GateRunner.available_gates())


def test_every_dimension_has_a_check():
    assert set(GATE_DIMENSION.values()) == set(DIMENSIONS)


def test_mapping_table_is_documented():
    doc = (Path(__file__).parents[2] / "docs" / "SCORECARD.md").read_text(encoding="utf-8")
    for gate, dim in GATE_DIMENSION.items():
        assert f"| `{gate}` | {dim} |" in doc, gate


# -- scores -----------------------------------------------------------------------------------


def test_clean_data_scores_100(gates):
    card = build_scorecard(run(gates, tables(False)), tables(False), schema=gates)
    d = card.dimensions
    assert d["completeness"].score == 100.0
    assert d["uniqueness"].score == 100.0
    assert d["consistency"].score == 100.0
    assert d["conformity"].score == 100.0
    assert d["accuracy"].score is None  # no range or distribution check ran
    assert d["timeliness"].score is None
    assert card.overall == 100.0


def test_failures_lower_the_right_dimension(gates):
    t = tables()
    card = build_scorecard(run(gates, t), t, schema=gates)
    d = card.dimensions
    # four non-nullable columns are checked (the nullable one is not): one has 1 null in 4 rows
    assert d["completeness"].score == 93.75
    assert d["uniqueness"].score == 87.5  # 75 for customer (1 repeated id in 4), 100 for order
    assert d["consistency"].score == 75.0  # 1 orphan in 4 orders
    assert d["conformity"].score == 100.0


def test_dimension_score_is_mean_of_its_checks(gates):
    t = tables()
    card = build_scorecard(run(gates, t), t, schema=gates)
    u = card.dimensions["uniqueness"]
    assert sorted(c.score for c in u.checks) == [75.0, 100.0]
    assert u.score == 87.5


def test_range_gate_feeds_accuracy(gates):
    t = tables()
    r = run(
        gates,
        t,
        {
            "format": "shape-verify-config",
            "version": 1,
            "ranges": {"customer.age": {"min": 0, "max": 120}},
        },
    )
    card = build_scorecard(
        r, t, schema=gates, config=r_config({"customer.age": {"min": 0, "max": 120}})
    )
    assert card.dimensions["accuracy"].score == 75.0  # 1 above max out of 4 rows


def r_config(ranges):
    from shape.quality import VerifyConfig

    return VerifyConfig.from_dict({"format": "shape-verify-config", "version": 1, "ranges": ranges})


def test_non_row_gate_is_binary(gates):
    t = tables(False)
    t["customer"] = t["customer"].drop(["age"])
    r = run(gates, t)
    card = build_scorecard(r, t, schema=gates)
    assert card.dimensions["conformity"].score == 0.0


def test_to_dict_has_format_and_version(gates):
    t = tables()
    d = build_scorecard(run(gates, t), t, schema=gates).to_dict()
    assert d["format"] == "shape-scorecard" and d["version"] == 1
    assert list(d["dimensions"]) == list(DIMENSIONS)
    json.dumps(d)


def test_json_output_has_no_row_values_by_default():
    from shape.quality import GateSchema
    from shape.quality.gatespec import ColumnSpec, TableSpec

    schema = GateSchema(
        tables={
            "u": TableSpec("u", {"email": ColumnSpec("email", "string")}, primary_key=("email",))
        }
    )
    t = {"u": pa.table({"email": ["ann@x.io", "ann@x.io", "bob@x.io"]})}
    card = build_scorecard(run(schema, t), t, schema=schema)
    assert card.dimensions["uniqueness"].score < 100
    assert card.samples
    assert "@x.io" not in card.to_json() and "@x.io" not in card.to_markdown()
    shown = build_scorecard(run(schema, t), t, schema=schema, show_classified=True)
    assert "ann@x.io" in shown.to_json()


def test_markdown_lists_every_dimension(gates):
    t = tables()
    md = build_scorecard(run(gates, t), t, schema=gates).to_markdown()
    for dim in DIMENSIONS:
        assert dim in md
    assert "# Shape data quality scorecard" in md
    assert "| Dimension | Score |" in md


def test_samples_included_and_limited(gates):
    t = tables()
    card = build_scorecard(run(gates, t), t, schema=gates, samples=1)
    per_check = {}
    for s in card.samples:
        per_check.setdefault((s["table"], s["gate"], tuple(s["values"])), []).append(s)
    assert card.samples and all(len(v) <= 1 for v in per_check.values())
    assert build_scorecard(run(gates, t), t, schema=gates, samples=0).samples == []


# -- owners -----------------------------------------------------------------------------------


def test_column_owners_none_without_project_file(tmp_path):
    assert column_owners(tmp_path) is None


def test_column_owners_read_from_project_file(tmp_path):
    (tmp_path / "shape.yml").write_text("owners:\n  customer.name: data-team\n")
    assert column_owners(tmp_path) == {"customer.name": "data-team"}


def test_column_owners_empty_when_file_has_none(tmp_path):
    (tmp_path / "shape.yml").write_text("name: demo\n")
    assert column_owners(tmp_path) == {}


def test_owners_attached_to_checks_and_known_missing_ok(gates):
    t = tables()
    card = build_scorecard(run(gates, t), t, schema=gates, owners={"customer.name": "data-team"})
    chk = [c for c in card.dimensions["completeness"].checks if c.column == "name"][0]
    assert chk.owner == "data-team"
    other = [c for c in card.dimensions["uniqueness"].checks if c.table == "order"][0]
    assert other.owner is None
    none = build_scorecard(run(gates, t), t, schema=gates, owners=None)
    assert all(c.owner is None for d in none.dimensions.values() for c in d.checks)


# -- snooze and suppress ----------------------------------------------------------------------


def write_supp(tmp_path, entries, version=1):
    p = tmp_path / "supp.json"
    p.write_text(
        json.dumps(
            {"format": "shape-scorecard-suppressions", "version": version, "entries": entries}
        )
    )
    return p


def test_suppress_excludes_known_issue_from_score(gates, tmp_path):
    t = tables()
    p = write_supp(
        tmp_path,
        [
            {
                "action": "suppress",
                "check": "null_constraint",
                "table": "customer",
                "column": "name",
                "reason": "legacy rows",
            }
        ],
    )
    card = build_scorecard(run(gates, t), t, schema=gates, suppressions=load_suppressions(p))
    assert card.dimensions["completeness"].score == 100.0
    assert card.known_issues[0]["action"] == "suppress"
    assert card.known_issues[0]["reason"] == "legacy rows"
    assert card.known_issues[0]["failing"] == 1


def test_snooze_applies_until_date_then_expires(gates, tmp_path):
    t = tables()
    until = date(2026, 11, 1)
    p = write_supp(
        tmp_path,
        [
            {
                "action": "snooze",
                "check": "unique_constraint",
                "table": "customer",
                "until": until.isoformat(),
                "reason": "fix due",
            }
        ],
    )
    entries = load_suppressions(p)
    r = run(gates, t)
    during = build_scorecard(r, t, schema=gates, suppressions=entries, today=until)
    assert during.dimensions["uniqueness"].score == 100.0
    after = build_scorecard(
        r, t, schema=gates, suppressions=entries, today=until + timedelta(days=1)
    )
    assert after.dimensions["uniqueness"].score == 87.5
    assert after.known_issues == []


def test_suppression_wildcards_and_non_matching(gates, tmp_path):
    t = tables()
    wide = write_supp(
        tmp_path, [{"action": "suppress", "check": "unique_constraint", "reason": "r"}]
    )
    card = build_scorecard(run(gates, t), t, schema=gates, suppressions=load_suppressions(wide))
    assert card.dimensions["uniqueness"].score == 100.0
    other = write_supp(
        tmp_path,
        [{"action": "suppress", "check": "unique_constraint", "table": "order", "reason": "r"}],
    )
    card = build_scorecard(run(gates, t), t, schema=gates, suppressions=load_suppressions(other))
    assert card.dimensions["uniqueness"].score == 87.5


def test_suppression_does_not_hide_passing_checks(gates, tmp_path):
    t = tables(False)
    p = write_supp(tmp_path, [{"action": "suppress", "check": "null_constraint", "reason": "r"}])
    card = build_scorecard(run(gates, t), t, schema=gates, suppressions=load_suppressions(p))
    assert card.known_issues == []
    assert card.dimensions["completeness"].score == 100.0


def test_suppressions_roundtrip_and_format(tmp_path):
    p = write_supp(
        tmp_path,
        [{"action": "snooze", "check": "null_constraint", "until": "2027-01-01", "reason": "r"}],
    )
    entries = load_suppressions(p)
    out = tmp_path / "out.json"
    save_suppressions(out, entries)
    doc = json.loads(out.read_text())
    assert doc["format"] == "shape-scorecard-suppressions" and doc["version"] == 1
    assert load_suppressions(out) == entries


@pytest.mark.parametrize(
    "doc,match",
    [
        ({"format": "other", "version": 1, "entries": []}, "format"),
        ({"format": "shape-scorecard-suppressions", "version": 2, "entries": []}, "newer"),
        ({"format": "shape-scorecard-suppressions", "version": "1", "entries": []}, "version"),
        ({"format": "shape-scorecard-suppressions", "version": 0, "entries": []}, "version"),
        ({"format": "shape-scorecard-suppressions", "version": 1, "entries": {}}, "entries"),
        (
            {
                "format": "shape-scorecard-suppressions",
                "version": 1,
                "entries": [{"action": "snooze", "check": "null_constraint", "reason": "r"}],
            },
            "until",
        ),
        (
            {
                "format": "shape-scorecard-suppressions",
                "version": 1,
                "entries": [
                    {"action": "snooze", "check": "null_constraint", "until": "soon", "reason": "r"}
                ],
            },
            "until",
        ),
        (
            {
                "format": "shape-scorecard-suppressions",
                "version": 1,
                "entries": [{"action": "ignore", "check": "null_constraint", "reason": "r"}],
            },
            "action",
        ),
        (
            {
                "format": "shape-scorecard-suppressions",
                "version": 1,
                "entries": [{"action": "suppress", "check": "null_constraint"}],
            },
            "reason",
        ),
        (
            {
                "format": "shape-scorecard-suppressions",
                "version": 1,
                "entries": [{"action": "suppress", "check": "nope", "reason": "r"}],
            },
            "check",
        ),
        (
            {
                "format": "shape-scorecard-suppressions",
                "version": 1,
                "entries": [
                    {"action": "suppress", "check": "null_constraint", "reason": "r", "x": 1}
                ],
            },
            "unknown",
        ),
    ],
)
def test_bad_suppression_files_are_refused(tmp_path, doc, match):
    p = tmp_path / "s.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(SuppressionError, match=match):
        load_suppressions(p)


def test_suppression_file_not_json(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("{")
    with pytest.raises(SuppressionError, match="JSON"):
        load_suppressions(p)


def test_v1_suppression_fixture_still_loads():
    fixture = Path(__file__).parent / "fixtures" / "suppressions_v1.json"
    entries = load_suppressions(fixture)
    assert [e.action for e in entries] == ["suppress", "snooze"]


# -- trends -----------------------------------------------------------------------------------


def test_trend_over_registry_history(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    r1 = build_scorecard(run(gates, tables()), tables(), schema=gates)
    r2 = build_scorecard(run(gates, tables(False)), tables(False), schema=gates)
    record_scorecard(reg, "orders", r1)
    record_scorecard(reg, "orders", r2)
    pts = scorecard_trend(reg, "orders")
    assert len(pts) == 2
    assert pts[0]["dimensions"]["completeness"] == 93.75
    assert pts[1]["dimensions"]["completeness"] == 100.0


def test_trend_in_card(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    record_scorecard(reg, "orders", build_scorecard(run(gates, tables()), tables(), schema=gates))
    card = build_scorecard(
        run(gates, tables(False)),
        tables(False),
        schema=gates,
        history=scorecard_trend(reg, "orders"),
    )
    t = card.trend["completeness"]
    assert t["previous"] == 93.75 and t["change"] == 6.25 and t["direction"] == "improving"
    assert card.trend["accuracy"]["direction"] == "no data"


def test_trend_directions(gates):
    t = tables(False)
    r = run(gates, t)
    hist = [{"at": "x", "content_id": "c", "dimensions": {d: 100.0 for d in DIMENSIONS}}]
    card = build_scorecard(r, t, schema=gates, history=hist)
    assert card.trend["completeness"]["direction"] == "steady"
    hist = [{"at": "x", "content_id": "c", "dimensions": {d: 100.0 for d in DIMENSIONS}}]
    bad = tables()
    card = build_scorecard(run(gates, bad), bad, schema=gates, history=hist)
    assert card.trend["completeness"]["direction"] == "declining"


def test_empty_history_has_no_trend(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    assert scorecard_trend(reg, "never") == []


def test_trend_skips_foreign_entries(gates, tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    reg.commit("scorecard-x", json.dumps({"format": "other"}))
    assert scorecard_trend(reg, "x") == []


def test_newer_scorecard_version_in_history_is_refused(tmp_path):
    reg = LocalRegistry(tmp_path / "reg")
    reg.commit(
        "scorecard-x", json.dumps({"format": "shape-scorecard", "version": 9, "dimensions": {}})
    )
    with pytest.raises(ScorecardError, match="newer"):
        scorecard_trend(reg, "x")


# -- HUNT2-quality ----------------------------------------------------------------------------


def _pk_schema():
    from shape.quality.gatespec import GateSchema

    return GateSchema.from_dict(
        {
            "format": "shape-gates",
            "version": 1,
            "tables": {"t": {"columns": {"id": {"type": "integer"}}, "primary_key": ["id"]}},
        }
    )


@pytest.mark.parametrize("rows", [1_000, 200_000])
def test_one_failing_row_never_scores_100(rows):
    """#569: a rate below 0.005% used to round up to 100 and hide the failure."""
    ids = list(range(rows))
    ids[1] = ids[0]
    t = {"t": pa.table({"id": ids})}
    schema = _pk_schema()
    card = build_scorecard(VerifyRunner(schema).run(t), t, schema=schema)
    check = card.dimensions["uniqueness"].checks[0]
    assert check.failing == 1
    assert check.score < 100
    assert card.dimensions["uniqueness"].score < 100
    assert card.overall < 100
    assert f"| uniqueness | {card.dimensions['uniqueness'].score:g} |" in card.to_markdown()
    assert "## Failing checks" in card.to_markdown()


def test_clean_check_still_scores_exactly_100():
    t = {"t": pa.table({"id": list(range(200_000))})}
    schema = _pk_schema()
    card = build_scorecard(VerifyRunner(schema).run(t), t, schema=schema)
    assert card.dimensions["uniqueness"].score == 100.0
    assert card.overall == 100.0


def test_a_failed_gate_is_not_scored_100_when_other_checks_ran():
    """#570: a relationship whose child column is missing was dropped from the score."""
    from shape.quality.gatespec import GateSchema

    schema = GateSchema.from_dict(
        {
            "format": "shape-gates",
            "version": 1,
            "tables": {
                "p": {"columns": {"id": {"type": "integer"}}, "primary_key": ["id"]},
                "c": {"columns": {"pid": {"type": "integer"}}},
                "e": {"columns": {"pid": {"type": "integer"}}},
            },
            "relationships": [
                {
                    "name": "r1",
                    "parent": "p",
                    "child": "c",
                    "parent_columns": ["id"],
                    "child_columns": ["pid"],
                    "type": "one_to_many",
                },
                {
                    "name": "r2",
                    "parent": "p",
                    "child": "e",
                    "parent_columns": ["id"],
                    "child_columns": ["pid"],
                    "type": "one_to_many",
                },
            ],
        }
    )
    t = {
        "p": pa.table({"id": [1, 2]}),
        "c": pa.table({"pid": [1, 2]}),
        "e": pa.table({"other": [1]}),
    }
    result = VerifyRunner(schema).run(t)
    assert not next(g for g in result.gate_results if g.gate_name == "referential_integrity").passed
    card = build_scorecard(result, t, schema=schema)
    assert card.dimensions["consistency"].score < 100
    assert any(c.score == 0 for c in card.dimensions["consistency"].checks)


def test_a_passing_gate_adds_no_binary_check():
    t = {"t": pa.table({"id": [1, 2, 3]})}
    schema = _pk_schema()
    card = build_scorecard(VerifyRunner(schema).run(t), t, schema=schema)
    assert [c.table for c in card.dimensions["uniqueness"].checks] == ["t"]
