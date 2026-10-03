"""W1-12: the planned-change registry (``shape-changes.yml``): format, matching, ``shape.diff``,
``shape check``, ``shape verify``, and ``shape changes``."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main
from shape.project.changes import (
    FORMAT,
    VERSION,
    ChangesError,
    ChangesVersionError,
    PlannedChanges,
    load_changes,
    parse_changes,
    problems,
    schema,
)
from shape.schemacheck import validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "planned_changes"
HEAD = f"format: {FORMAT}\nversion: 1\nchanges:\n"
IN_WINDOW = "2026-11-15"


def entry(**kw) -> str:
    base = {
        "id": "e1",
        "column": "note",
        "kinds": "[null_rate_change]",
        "from": "2026-11-01",
        "until": "2026-11-30",
        "reason": "release",
    }
    base.update(kw)
    lines = [
        f"  - {k}: {v}" if i == 0 else f"    {k}: {v}" for i, (k, v) in enumerate(base.items())
    ]
    return "\n".join(lines) + "\n"


def parse(body: str) -> PlannedChanges:
    return parse_changes(HEAD + body, "shape-changes.yml")


# ---- the file: format, schema, compatibility -------------------------------------------------


def test_capsule_loads_and_conforms_to_the_schema():
    pc = load_changes(FIXTURES / "v1" / "shape-changes.yml")
    assert pc.version == 1 and [e.id for e in pc.entries] == [
        "orders-new-column-2026-11",
        "amount-type-migration",
        "legacy-notes-noise",
        "vendor-switch",
    ]
    assert pc.entries[3].action == "expect"  # the default
    assert pc.entries[0].from_ == date(2026, 11, 1) and pc.entries[0].until == date(2026, 11, 30)
    assert validate(pc.document, schema()) == []


def test_schema_is_shipped_and_declares_format_and_version():
    from importlib import resources

    text = (
        resources.files("shape").joinpath("schemas/shape-planned-changes-v1.schema.json")
    ).read_text(encoding="utf-8")
    s = json.loads(text)
    assert s == schema()
    assert s["properties"]["format"] == {"const": FORMAT}
    assert s["properties"]["version"]["type"] == "integer"
    assert VERSION == 1


def test_json_file_works(tmp_path):
    doc = {
        "format": FORMAT,
        "version": 1,
        "changes": [
            {
                "id": "a",
                "column": "x",
                "kinds": ["column_added"],
                "until": "2026-12-01",
                "reason": "r",
            }
        ],
    }
    f = tmp_path / "c.json"
    f.write_text(json.dumps(doc), encoding="utf-8")
    assert load_changes(f).entries[0].until == date(2026, 12, 1)


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        (entry(kinds="[bogus_kind]"), "kinds[0]"),
        (entry(reason='""'), "reason"),
        (entry(id="Bad ID"), "id"),
        (entry() + entry(), "duplicate"),
        (entry(**{"from": "2026-12-01", "until": "2026-11-01"}), "until"),
        (entry(until="2026-13-45"), "until"),
        (entry(action="severity"), "severity"),  # severity action without a severity
        (entry(action="expect", severity="high"), "severity"),  # severity without the action
        (entry(action="mute"), "action"),
        (entry(kinds="[]"), "kinds"),
        (entry(zzz="1"), "zzz"),
        ("".join(ln for ln in entry().splitlines(True) if "until" not in ln), "until"),
        ("".join(ln for ln in entry().splitlines(True) if "reason" not in ln), "reason"),
    ],
)
def test_invalid_entries_are_reported_with_id_and_key(body, needle):
    with pytest.raises(ChangesError) as exc:
        parse(body)
    assert any(needle in p for p in exc.value.problems), exc.value.problems


def test_all_problems_come_at_once_and_name_the_entry():
    with pytest.raises(ChangesError) as exc:
        parse(entry(kinds="[nope]", reason='""'))
    assert len(exc.value.problems) >= 2
    assert all("e1" in p or "changes[0]" in p for p in exc.value.problems)


def test_newer_version_is_refused_with_the_policy_message():
    with pytest.raises(ChangesVersionError) as exc:
        parse_changes(f"format: {FORMAT}\nversion: 2\nsomething: new\n", "c.yml")
    assert "version 2 is newer than this Shape understands" in str(exc.value)
    assert "upgrade Shape" in str(exc.value)
    assert problems({"format": FORMAT, "version": 99}) == [
        "version 99 is newer than this Shape understands (it reads up to version 1): "
        "upgrade Shape, or lower the file's version if it uses nothing newer"
    ]


def test_wrong_format_and_empty_and_not_a_mapping():
    for text in ("", "- a\n- b\n", "format: shape-project\nversion: 1\nchanges: []\n"):
        with pytest.raises(ChangesError):
            parse_changes(text, "c.yml")


def test_contract_rule_names_are_valid_kinds():
    pc = parse(entry(kinds="[unique, not_null, dtype]"))
    assert pc.entries[0].kinds == ("unique", "not_null", "dtype")


# ---- matching: window, boundaries, globs, sources --------------------------------------------


def match(pc, on, *, column="note", kind="null_rate_change", table="orders", source=None):
    ap = pc.applier(on=on, source=source)
    return ap.match(table, column, kind), ap


def test_window_bounds_are_inclusive():
    pc = parse(entry())
    assert match(pc, "2026-11-01")[0].id == "e1"
    assert match(pc, "2026-11-30")[0].id == "e1"
    assert match(pc, "2026-10-31")[0] is None
    hit, ap = match(pc, "2026-12-01")
    assert hit is None and [e["id"] for e in ap.report()["expired"]] == ["e1"]


def test_not_yet_started_entry_is_neither_planned_nor_expired():
    hit, ap = match(parse(entry()), "2026-10-31")
    assert hit is None and ap.report()["expired"] == []


def test_no_from_means_active_from_the_beginning():
    pc = parse("".join(ln for ln in entry().splitlines(True) if "from" not in ln))
    assert match(pc, "1999-01-01")[0].id == "e1"


@pytest.mark.parametrize(
    ("pattern", "column", "table", "expected"),
    [
        ("note", "note", "orders", True),
        ("orders.note", "note", "orders", True),
        ("billing.note", "note", "orders", False),
        ("not*", "note", "orders", True),
        ("orders.*", "note", "orders", True),
        ("amount*", "note", "orders", False),
        ("*", "note", "orders", True),
        ("*", None, "orders", True),  # a table-level change
        ("orders", None, "orders", True),
        ("billing", None, "orders", False),
    ],
)
def test_column_patterns(pattern, column, table, expected):
    pc = parse(entry(column=f'"{pattern}"'))
    hit, _ = match(pc, IN_WINDOW, column=column, table=table)
    assert (hit is not None) is expected


def test_kind_must_be_listed():
    hit, _ = match(parse(entry()), IN_WINDOW, kind="dtype_change")
    assert hit is None


def test_source_must_match_when_the_entry_names_one():
    pc = parse(entry(source="orders"))
    assert match(pc, IN_WINDOW, source="orders")[0] is not None
    assert match(pc, IN_WINDOW, source="events")[0] is None
    assert match(pc, IN_WINDOW, source=None)[0] is None
    assert match(parse(entry()), IN_WINDOW, source="events")[0] is not None


def test_not_null_names_the_nullable_rule():
    pc = parse(entry(kinds="[not_null]"))
    assert match(pc, IN_WINDOW, kind="nullable")[0] is not None


def test_first_matching_entry_wins():
    pc = parse(entry(id="a", action="suppress") + entry(id="b"))
    assert match(pc, IN_WINDOW)[0].id == "a"


def test_planned_not_observed_lists_only_active_expect_entries_without_a_match():
    pc = parse(
        entry(id="seen")
        + entry(id="unseen", column="other")
        + entry(id="muted", column="other", action="suppress")
        + entry(id="late", column="other", **{"from": "2027-01-01", "until": "2027-02-01"})
    )
    ap = pc.applier(on=IN_WINDOW)
    ap.match("orders", "note", "null_rate_change")
    rep = ap.report()
    assert [e["id"] for e in rep["planned"]] == ["seen"] and rep["planned"][0]["matches"] == 1
    assert [e["id"] for e in rep["planned_not_observed"]] == ["unseen"]


# ---- shape.diff(planned=...) -----------------------------------------------------------------


def orders(n: int, *, null_notes: int = 0, extra: bool = False, rows_factor: int = 1) -> pa.Table:
    n *= rows_factor
    cols = {
        "id": list(range(n)),
        "status": [["new", "paid", "paid", "shipped"][i % 4] for i in range(n)],
        "note": [None if i < null_notes else f"note {i}" for i in range(n)],
    }
    if extra:
        cols["loyalty_tier"] = [["gold", "silver"][i % 2] for i in range(n)]
    return pa.table(cols)


@pytest.fixture()
def profiles():
    base = shape.profile(orders(200))
    cur = shape.profile(orders(200, null_notes=50, extra=True))
    return base, cur


def kinds_of(result):
    return {(c["column"], c["kind"]) for c in result.changes}


def test_baseline_has_the_planned_kinds(profiles):
    base, cur = profiles
    got = kinds_of(shape.diff(base, cur))
    assert ("note", "null_rate_change") in got and ("loyalty_tier", "column_added") in got


def test_expect_marks_and_does_not_count(profiles):
    base, cur = profiles
    pc = parse(
        entry(id="n", column="note") + entry(id="c", column="loyalty_tier", kinds="[column_added]")
    )
    r = shape.diff(base, cur, planned=pc, on=IN_WINDOW)
    assert r.drifted is False
    assert {c["planned"]["id"] for c in r.changes} == {"n", "c"}
    assert all(c["planned"]["action"] == "expect" for c in r.changes)
    assert {e["id"] for e in r.planned} == {"n", "c"}
    assert r.planned_not_observed == [] and r.expired == []
    assert r.to_dict()["planned"] and r.to_dict()["drifted"] is False


def test_one_day_after_until_it_fails_and_reports_expiry(profiles):
    base, cur = profiles
    pc = parse(entry(id="n", column="note", until="2026-11-30"))
    r = shape.diff(base, cur, planned=pc, on="2026-12-01")
    assert r.drifted is True
    assert all("planned" not in c for c in r.changes)
    assert [e["id"] for e in r.expired] == ["n"]


def test_boundary_days(profiles):
    base, cur = profiles
    pc = parse(entry(id="n", column="note", kinds="[null_rate_change]"))
    assert shape.diff(base, cur, planned=pc, on="2026-11-01").planned
    assert shape.diff(base, cur, planned=pc, on="2026-11-30").planned
    assert shape.diff(base, cur, planned=pc, on="2026-10-31").planned == []


def test_unplanned_change_next_to_a_planned_one_still_fails(profiles):
    base, cur = profiles
    pc = parse(entry(id="n", column="note"))
    r = shape.diff(base, cur, planned=pc, on=IN_WINDOW)
    assert r.drifted is True
    assert ("loyalty_tier", "column_added") in kinds_of(r)
    planned = [c for c in r.changes if "planned" in c]
    assert [c["column"] for c in planned] == ["note"]


def test_glob_matches_and_non_matching_glob_does_not(profiles):
    base, cur = profiles
    hit = shape.diff(base, cur, planned=parse(entry(column='"no*"')), on=IN_WINDOW)
    assert any(c["column"] == "note" and "planned" in c for c in hit.changes)
    miss = shape.diff(base, cur, planned=parse(entry(column='"zz*"')), on=IN_WINDOW)
    assert miss.planned == [] and all("planned" not in c for c in miss.changes)


def test_suppress_removes_the_change(profiles):
    base, cur = profiles
    pc = parse(
        entry(id="s", column="note", action="suppress")
        + entry(id="c", column="loyalty_tier", kinds="[column_added]", action="suppress")
    )
    r = shape.diff(base, cur, planned=pc, on=IN_WINDOW)
    assert r.changes == [] and r.drifted is False
    assert {e["id"] for e in r.planned} == {"s", "c"}


def test_severity_action_sets_the_severity_and_counts_against_min_severity(profiles):
    base, cur = profiles
    pc = parse(entry(id="v", column="note", action="severity", severity="high"))
    r = shape.diff(
        base,
        cur,
        planned=pc,
        on=IN_WINDOW,
        thresholds={"min_severity": "high"},
        ignore_columns=["loyalty_tier"],
    )
    note = [c for c in r.changes if c["column"] == "note"]
    assert (
        note
        and note[0]["severity"] == "high"
        and note[0]["planned"] == {"id": "v", "action": "severity"}
    )
    assert r.drifted is True
    # lowered below min_severity: reported, not counted
    low = parse(entry(id="v", column="note", action="severity", severity="low"))
    r = shape.diff(
        base,
        cur,
        planned=low,
        on=IN_WINDOW,
        thresholds={"min_severity": "medium"},
        ignore_columns=["loyalty_tier"],
    )
    assert r.drifted is False and r.changes[0]["severity"] == "low"


def test_expect_on_a_change_below_min_severity_is_still_matched(profiles):
    base, cur = profiles
    pc = parse(entry(id="n", column="note"))  # null_rate_change is medium
    r = shape.diff(base, cur, planned=pc, on=IN_WINDOW, thresholds={"min_severity": "high"})
    assert [e["id"] for e in r.planned_not_observed] == []  # matched, even though filtered


def test_planned_not_observed_is_informational(profiles):
    base, _ = profiles
    pc = parse(entry(id="gone", column="loyalty_tier", kinds="[column_added]"))
    r = shape.diff(base, base, planned=pc, on=IN_WINDOW)
    assert r.drifted is False
    assert [e["id"] for e in r.planned_not_observed] == ["gone"]


def test_diff_accepts_a_path_and_a_list_of_entries(profiles, tmp_path):
    base, cur = profiles
    f = tmp_path / "c.yml"
    f.write_text(HEAD + entry(id="n", column="note"), encoding="utf-8")
    assert shape.diff(base, cur, planned=f, on=IN_WINDOW).planned
    raw = [
        {
            "id": "n",
            "column": "note",
            "kinds": ["null_rate_change"],
            "until": "2026-11-30",
            "reason": "r",
        }
    ]
    assert shape.diff(base, cur, planned=raw, on=date(2026, 11, 15)).planned


def test_diff_without_planned_is_unchanged(profiles):
    base, cur = profiles
    r = shape.diff(base, cur)
    assert r.to_dict().keys() == {"drifted", "changes"}


def test_bad_on_date_is_an_error(profiles):
    base, cur = profiles
    with pytest.raises(ValueError, match="2026-13-01"):
        shape.diff(base, cur, planned=parse(entry()), on="2026-13-01")


# ---- the CLI ---------------------------------------------------------------------------------


def write_orders_csv(path: Path, null_notes=0, extra=False) -> Path:
    t = orders(200, null_notes=null_notes, extra=extra)
    import pyarrow.csv as pcsv

    pcsv.write_csv(t, path)
    return path


@pytest.fixture()
def work(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    paths = {}
    for name, kw in {"base": {}, "cur": {"null_notes": 50, "extra": True}}.items():
        csv = write_orders_csv(tmp_path / f"{name}.csv", **kw)
        assert (
            main(["profile", str(csv), "-o", str(tmp_path / f"{name}.shape"), "--no-project"]) == 0
        )
        paths[name] = tmp_path / f"{name}.shape"
    capsys.readouterr()
    return tmp_path, paths["base"], paths["cur"]


def run_diff(capsys, *args):
    capsys.readouterr()
    rc = main(["diff", *args])
    cap = capsys.readouterr()
    out = cap.out.strip()
    return rc, (json.loads(out) if out else None), cap.err


def both_planned(root: Path) -> Path:
    f = root / "shape-changes.yml"
    f.write_text(
        HEAD
        + entry(id="n", column="note")
        + entry(id="c", column="loyalty_tier", kinds="[column_added]"),
        encoding="utf-8",
    )
    return f


def test_cli_planned_inside_the_window_exits_zero(work, capsys):
    root, base, cur = work
    f = both_planned(root)
    rc, out, err = run_diff(
        capsys, str(base), str(cur), "--fail-on-drift", "--changes", str(f), "--on", IN_WINDOW
    )
    assert rc == 0 and out["drifted"] is False
    assert {p["id"] for p in out["planned"]} == {"n", "c"}
    assert out["planned_not_observed"] == [] and out["expired"] == []
    assert "(planned: n)" in err and "(planned: c)" in err


def test_cli_one_day_after_until_fails_with_a_warning(work, capsys):
    root, base, cur = work
    f = both_planned(root)
    rc, out, err = run_diff(
        capsys, str(base), str(cur), "--fail-on-drift", "--changes", str(f), "--on", "2026-12-01"
    )
    assert rc == 1 and out["drifted"] is True
    assert "shape: warning: planned change n expired on 2026-11-30" in err
    assert {e["id"] for e in out["expired"]} == {"n", "c"}


def test_cli_unplanned_next_to_planned_fails(work, capsys):
    root, base, cur = work
    f = root / "shape-changes.yml"
    f.write_text(HEAD + entry(id="n", column="note"), encoding="utf-8")
    rc, out, _ = run_diff(
        capsys, str(base), str(cur), "--fail-on-drift", "--changes", str(f), "--on", IN_WINDOW
    )
    assert rc == 1 and out["drifted"] is True


def test_cli_default_file_next_to_shape_yml_and_flags(work, capsys):
    root, base, cur = work
    (root / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n  orders: {path: data}\n", encoding="utf-8"
    )
    both_planned(root)  # shape-changes.yml next to shape.yml
    rc, out, _ = run_diff(capsys, str(base), str(cur), "--fail-on-drift", "--on", IN_WINDOW)
    assert rc == 0 and out["planned"]
    rc, out, _ = run_diff(
        capsys, str(base), str(cur), "--fail-on-drift", "--on", IN_WINDOW, "--no-changes"
    )
    assert rc == 1 and "planned" not in out
    other = root / "other.yml"
    other.write_text(HEAD + entry(id="only", column="note"), encoding="utf-8")
    rc, out, _ = run_diff(capsys, str(base), str(cur), "--on", IN_WINDOW, "--changes", str(other))
    assert [p["id"] for p in out["planned"]] == ["only"]  # --changes overrides the default


def test_cli_changes_key_in_shape_yml(work, capsys):
    root, base, cur = work
    (root / "plans").mkdir()
    (root / "plans" / "p.yml").write_text(
        HEAD
        + entry(id="k", column="note")
        + entry(id="c", column="loyalty_tier", kinds="[column_added]"),
        encoding="utf-8",
    )
    (root / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nchanges: plans/p.yml\n"
        "sources:\n  orders: {path: data}\n",
        encoding="utf-8",
    )
    rc, out, _ = run_diff(capsys, str(base), str(cur), "--fail-on-drift", "--on", IN_WINDOW)
    assert rc == 0 and {p["id"] for p in out["planned"]} == {"k", "c"}
    assert out["project"]["changes"].endswith("p.yml")


def test_cli_source_entries_follow_the_source(work, capsys):
    root, base, cur = work
    (root / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n  orders: {path: data}\n"
        "  events: {path: ev}\n",
        encoding="utf-8",
    )
    f = root / "shape-changes.yml"
    f.write_text(HEAD + entry(id="n", column="note", source="orders"), encoding="utf-8")
    _, out, _ = run_diff(capsys, str(base), str(cur), "--on", IN_WINDOW, "--source", "orders")
    assert [p["id"] for p in out["planned"]] == ["n"]
    _, out, _ = run_diff(capsys, str(base), str(cur), "--on", IN_WINDOW, "--source", "events")
    assert out["planned"] == []


def test_cli_invalid_file_is_exit_2_for_diff_and_1_for_validate(work, capsys):
    root, base, cur = work
    f = root / "bad.yml"
    f.write_text(HEAD + entry(kinds="[nope]", reason='""'), encoding="utf-8")
    rc, _, err = run_diff(capsys, str(base), str(cur), "--changes", str(f))
    assert rc == 2 and "e1" in err
    capsys.readouterr()
    assert main(["changes", "validate", str(f)]) == 1
    err = capsys.readouterr().err
    lines = [ln for ln in err.splitlines() if "e1" in ln]
    assert (
        len(lines) >= 2
        and any("kinds" in ln for ln in lines)
        and any("reason" in ln for ln in lines)
    )


@pytest.mark.parametrize(
    "body",
    [
        entry(kinds="[nope]"),
        entry(reason='""'),
        entry() + entry(),
        entry(**{"from": "2026-12-01", "until": "2026-11-01"}),
    ],
)
def test_cli_each_invalid_kind_of_file(work, capsys, body):
    root, base, cur = work
    f = root / "bad.yml"
    f.write_text(HEAD + body, encoding="utf-8")
    assert main(["changes", "validate", str(f)]) == 1
    assert run_diff(capsys, str(base), str(cur), "--changes", str(f))[0] == 2


def test_cli_missing_changes_file_is_exit_2(work, capsys):
    root, base, cur = work
    assert run_diff(capsys, str(base), str(cur), "--changes", "nope.yml")[0] == 2
    capsys.readouterr()
    assert main(["changes", "validate", "nope.yml"]) == 2
    assert main(["changes", "validate"]) == 2  # no default file here


def test_cli_newer_version_refused(work, capsys):
    root, base, cur = work
    f = root / "c.yml"
    f.write_text(f"format: {FORMAT}\nversion: 2\nchanges: []\n", encoding="utf-8")
    rc, _, err = run_diff(capsys, str(base), str(cur), "--changes", str(f))
    assert rc == 2 and "version 2 is newer than this Shape understands" in err
    capsys.readouterr()
    assert main(["changes", "validate", str(f)]) == 1
    assert "newer than this Shape understands" in capsys.readouterr().err


def test_cli_bad_on_date_is_exit_2(work, capsys):
    root, base, cur = work
    assert (
        run_diff(capsys, str(base), str(cur), "--changes", str(both_planned(root)), "--on", "x")[0]
        == 2
    )


# ---- shape check ----------------------------------------------------------------------------


@pytest.fixture()
def contract(work):
    root = work[0]
    c = root / "contract.json"
    c.write_text(
        json.dumps({"columns": {"note": {"nullable": False}, "id": {"unique": True}}}),
        encoding="utf-8",
    )
    return c


def run_check(capsys, *args):
    capsys.readouterr()
    rc = main(["check", *args])
    cap = capsys.readouterr()
    return rc, json.loads(cap.out) if cap.out.strip() else None, cap.err


def test_check_planned_rule_failure_does_not_fail(work, contract, capsys):
    root, _, cur = work
    rc, out, _ = run_check(capsys, str(cur), str(contract))
    assert rc == 1 and any(v["rule"] == "nullable" for v in out["violations"])
    f = root / "c.yml"
    f.write_text(HEAD + entry(id="nn", column="note", kinds="[nullable]"), encoding="utf-8")
    rc, out, _ = run_check(capsys, str(cur), str(contract), "--changes", str(f), "--on", IN_WINDOW)
    assert rc == 0 and out["passed"] is True
    assert [v["planned"]["id"] for v in out["violations"] if "planned" in v] == ["nn"]
    assert [p["id"] for p in out["planned"]] == ["nn"]
    rc, out, err = run_check(
        capsys, str(cur), str(contract), "--changes", str(f), "--on", "2026-12-01"
    )
    assert rc == 1 and "planned change nn expired on 2026-11-30" in err and out["expired"]


def test_check_not_null_alias_and_suppress(work, contract, capsys):
    root, _, cur = work
    f = root / "c.yml"
    f.write_text(
        HEAD + entry(id="nn", column="note", kinds="[not_null]", action="suppress"),
        encoding="utf-8",
    )
    rc, out, _ = run_check(capsys, str(cur), str(contract), "--changes", str(f), "--on", IN_WINDOW)
    assert rc == 0 and out["violations"] == []


def test_check_other_failures_still_fail(work, contract, capsys):
    root, _, cur = work
    f = root / "c.yml"
    f.write_text(HEAD + entry(id="nn", column="status", kinds="[nullable]"), encoding="utf-8")
    rc, _, _ = run_check(capsys, str(cur), str(contract), "--changes", str(f), "--on", IN_WINDOW)
    assert rc == 1


def test_check_severity_entry_fails_only_at_high(work, contract, capsys):
    root, _, cur = work
    f = root / "c.yml"
    f.write_text(
        HEAD
        + entry(id="nn", column="note", kinds="[nullable]", action="severity", severity="medium"),
        encoding="utf-8",
    )
    rc, out, _ = run_check(capsys, str(cur), str(contract), "--changes", str(f), "--on", IN_WINDOW)
    assert rc == 0 and out["violations"][0]["severity"] == "medium"
    f.write_text(
        HEAD
        + entry(id="nn", column="note", kinds="[nullable]", action="severity", severity="high"),
        encoding="utf-8",
    )
    rc, _, _ = run_check(capsys, str(cur), str(contract), "--changes", str(f), "--on", IN_WINDOW)
    assert rc == 1


# ---- shape verify: the drift gate ------------------------------------------------------------


@pytest.fixture()
def lake(work):
    root = work[0]
    d = root / "lake"
    d.mkdir()
    pq.write_table(
        pa.table({"id": [1, 2], "extra": ["a", "b"], "amount": [1.5, 2.5]}), d / "orders.parquet"
    )
    (root / "verify.json").write_text(
        json.dumps(
            {
                "format": "shape-verify-config",
                "version": 1,
                "baseline": {
                    "orders": {"columns": {"id": "int64", "amount": "int64", "old": "string"}}
                },
            }
        ),
        encoding="utf-8",
    )
    return root


def run_verify(capsys, *args):
    capsys.readouterr()
    rc = main(["verify", "lake", "--config", "verify.json", *args])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_verify_drift_gate_fails_without_a_plan(lake, capsys):
    rc, out, err = run_verify(capsys, "--no-project")
    assert rc == 1 and "FAIL" in out and "old" in err


def test_verify_planned_breaking_changes_do_not_fail(lake, capsys):
    f = lake / "c.yml"
    f.write_text(
        HEAD
        + entry(id="rm", column="old", kinds="[column_removed]")
        + entry(id="ty", column="amount", kinds="[dtype_change]")
        + entry(id="ad", column="extra", kinds="[column_added]"),
        encoding="utf-8",
    )
    rc, out, err = run_verify(capsys, "--changes", str(f), "--on", IN_WINDOW)
    assert rc == 0 and "Result: PASS" in out
    assert "(planned: rm)" in out and "(planned: ty)" in out and "(planned: ad)" in out
    rc, out, err = run_verify(capsys, "--changes", str(f), "--on", "2026-12-01")
    assert rc == 1 and "planned change rm expired on 2026-11-30" in err


def test_verify_unplanned_change_next_to_planned_fails(lake, capsys):
    f = lake / "c.yml"
    f.write_text(HEAD + entry(id="rm", column="old", kinds="[column_removed]"), encoding="utf-8")
    rc, _, err = run_verify(capsys, "--changes", str(f), "--on", IN_WINDOW)
    assert rc == 1 and "amount" in err


def test_verify_suppress_hides_and_report_carries_planned(lake, capsys):
    f = lake / "c.yml"
    f.write_text(
        HEAD
        + entry(id="rm", column="old", kinds="[column_removed]", action="suppress")
        + entry(id="ty", column="amount", kinds="[dtype_change]"),
        encoding="utf-8",
    )
    report = lake / "r.json"
    rc, out, _ = run_verify(capsys, "--changes", str(f), "--on", IN_WINDOW, "-o", str(report))
    assert rc == 0 and "old" not in out
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert {p["id"] for p in doc["planned"]} == {"rm", "ty"}


def test_verify_observe_mode_and_planned_work_together(lake, capsys):
    (lake / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n  s: {path: lake}\n"
        "gates:\n  schema_drift: {mode: enforce}\n",
        encoding="utf-8",
    )
    f = lake / "shape-changes.yml"
    f.write_text(
        HEAD
        + entry(id="rm", column="old", kinds="[column_removed]")
        + entry(id="ty", column="amount", kinds="[dtype_change]"),
        encoding="utf-8",
    )
    rc, out, _ = run_verify(capsys, "--on", IN_WINDOW)
    assert rc == 0 and "Result: PASS" in out  # an enforced gate, a planned change


# ---- shape changes list / add / ack ----------------------------------------------------------


def test_list_active_on_and_json(work, capsys):
    root, _, _ = work
    f = both_planned(root)
    capsys.readouterr()
    assert main(["changes", "list", str(f), "--active-on", IN_WINDOW, "--json"]) == 0
    got = json.loads(capsys.readouterr().out)
    assert {e["id"] for e in got["changes"]} == {"n", "c"}
    assert main(["changes", "list", str(f), "--active-on", "2026-12-01", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["changes"] == []
    assert main(["changes", "list", str(f)]) == 0  # text, no date: all
    assert "n" in capsys.readouterr().out


def test_add_creates_and_appends_keeping_comments(work, capsys):
    root, _, _ = work
    f = root / "shape-changes.yml"
    f.write_text(
        "# our planned changes\nformat: shape-planned-changes\nversion: 1\nchanges:\n"
        "  # release train\n  - id:   keep-me   # odd spacing stays\n    column: note\n"
        "    kinds: [null_rate_change]\n    until: 2026-11-30\n    reason: first\n",
        encoding="utf-8",
    )
    before = f.read_text(encoding="utf-8")
    rc = main(
        [
            "changes",
            "add",
            "--file",
            str(f),
            "--id",
            "new-col",
            "--column",
            "loyalty_tier",
            "--kind",
            "column_added",
            "--kind",
            "table_added",
            "--until",
            "2026-12-31",
            "--reason",
            "Release: adds a column",
            "--from",
            "2026-11-01",
        ]
    )
    assert rc == 0
    after = f.read_text(encoding="utf-8")
    assert after.startswith(before)  # nothing existing was rewritten
    pc = load_changes(f)
    assert [e.id for e in pc.entries] == ["keep-me", "new-col"]
    e = pc.entries[1]
    assert e.kinds == ("column_added", "table_added") and e.reason == "Release: adds a column"
    assert e.action == "expect" and e.from_ == date(2026, 11, 1)


def test_add_duplicate_id_is_exit_2_and_file_unchanged(work, capsys):
    root, _, _ = work
    f = both_planned(root)
    before = f.read_text(encoding="utf-8")
    rc = main(
        [
            "changes",
            "add",
            "--file",
            str(f),
            "--id",
            "n",
            "--column",
            "x",
            "--kind",
            "column_added",
            "--until",
            "2026-12-31",
            "--reason",
            "r",
        ]
    )
    assert rc == 2 and f.read_text(encoding="utf-8") == before


@pytest.mark.parametrize(
    "extra",
    [
        ["--kind", "bogus"],
        ["--until", "2026-12-31", "--from", "2027-01-01"],
        ["--action", "severity"],
        ["--severity", "high"],
    ],
)
def test_add_invalid_is_exit_2_and_writes_nothing(work, extra):
    root, _, _ = work
    f = root / "c.yml"
    args = ["changes", "add", "--file", str(f), "--id", "x", "--column", "c", "--reason", "r"]
    kind = [] if "--kind" in extra else ["--kind", "column_added"]
    until = [] if "--until" in extra else ["--until", "2026-12-31"]
    assert main([*args, *kind, *until, *extra]) == 2
    assert not f.exists()


def test_add_creates_a_missing_file_next_to_shape_yml(work, capsys):
    root, _, _ = work
    (root / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nsources:\n  orders: {path: data}\n", encoding="utf-8"
    )
    assert (
        main(
            [
                "changes",
                "add",
                "--id",
                "a",
                "--column",
                "x",
                "--kind",
                "column_added",
                "--until",
                "2026-12-31",
                "--reason",
                "r",
                "--source",
                "orders",
            ]
        )
        == 0
    )
    pc = load_changes(root / "shape-changes.yml")
    assert pc.entries[0].source == "orders" and pc.entries[0].from_ is not None


def test_add_empty_list_and_json_files(work):
    root, _, _ = work
    f = root / "c.yml"
    f.write_text(f"format: {FORMAT}\nversion: 1\nchanges: []\n", encoding="utf-8")
    args = [
        "--id",
        "a",
        "--column",
        "x",
        "--kind",
        "column_added",
        "--until",
        "2026-12-31",
        "--reason",
        "r",
    ]
    assert main(["changes", "add", "--file", str(f), *args]) == 0
    assert load_changes(f).entries[0].id == "a"
    j = root / "c.json"
    j.write_text(json.dumps({"format": FORMAT, "version": 1, "changes": []}), encoding="utf-8")
    assert main(["changes", "add", "--file", str(j), *args]) == 0
    assert load_changes(j).entries[0].id == "a"


def test_ack_turns_reported_changes_into_expect_entries(work, capsys):
    root, base, cur = work
    result = root / "result.json"
    run_diff(capsys, str(base), str(cur), "--json", str(result), "--no-project")
    changes = json.loads(result.read_text(encoding="utf-8"))["changes"]
    assert len(changes) >= 2
    f = root / "c.yml"
    capsys.readouterr()
    rc = main(
        [
            "changes",
            "ack",
            str(result),
            "--file",
            str(f),
            "--until",
            "2026-12-31",
            "--reason",
            "reviewed in standup",
            "--by",
            "sam",
            "--all",
        ]
    )
    assert rc == 0
    pc = load_changes(f)
    assert len(pc.entries) == len(changes)
    for e in pc.entries:
        assert e.action == "expect" and e.acknowledged_by == "sam" and e.acknowledged_at is not None
        assert e.reason == "reviewed in standup"
    # and the acknowledged result no longer fails on the day
    rc, out, _ = run_diff(
        capsys, str(base), str(cur), "--changes", str(f), "--fail-on-drift", "--on", "2026-11-15"
    )
    assert rc == 0, out


def test_ack_selected_changes_only_and_ids_are_unique(work, capsys):
    root, base, cur = work
    result = root / "result.json"
    run_diff(capsys, str(base), str(cur), "--json", str(result), "--no-project")
    f = root / "c.yml"
    args = [
        "changes",
        "ack",
        str(result),
        "--file",
        str(f),
        "--until",
        "2026-12-31",
        "--reason",
        "r",
        "--by",
        "sam",
    ]
    assert main([*args, "--change", "1"]) == 0
    assert len(load_changes(f).entries) == 1
    assert main([*args, "--change", "1"]) == 0  # a second ack of the same change: new unique id
    ids = [e.id for e in load_changes(f).entries]
    assert len(ids) == 2 and len(set(ids)) == 2


@pytest.mark.parametrize(
    "sel", [[], ["--change", "99"], ["--change", "0"], ["--all", "--change", "1"]]
)
def test_ack_bad_selection_is_exit_2(work, capsys, sel):
    root, base, cur = work
    result = root / "result.json"
    run_diff(capsys, str(base), str(cur), "--json", str(result), "--no-project")
    f = root / "c.yml"
    rc = main(
        [
            "changes",
            "ack",
            str(result),
            "--file",
            str(f),
            "--until",
            "2026-12-31",
            "--reason",
            "r",
            "--by",
            "sam",
            *sel,
        ]
    )
    assert rc == 2 and not f.exists()


def test_ack_unreadable_result_is_exit_2(work):
    root, _, _ = work
    (root / "r.json").write_text("{not json", encoding="utf-8")
    assert (
        main(
            [
                "changes",
                "ack",
                str(root / "r.json"),
                "--file",
                str(root / "c.yml"),
                "--until",
                "2026-12-31",
                "--reason",
                "r",
                "--by",
                "s",
                "--all",
            ]
        )
        == 2
    )


# ---- the `changes` key of shape.yml ----------------------------------------------------------


def test_project_changes_key_is_validated_and_resolved(tmp_path):
    from shape.project import ProjectError, parse_project

    head = "format: shape-project\nversion: 1\nsources:\n  a: {path: x}\n"
    p = parse_project(head + "changes: plans/p.yml\n", tmp_path / "shape.yml")
    assert p.changes_file() == tmp_path / "plans" / "p.yml"
    assert parse_project(head, tmp_path / "shape.yml").changes_file() is None
    (tmp_path / "shape-changes.yml").write_text("x", encoding="utf-8")
    assert parse_project(head, tmp_path / "shape.yml").changes_file() == (
        tmp_path / "shape-changes.yml"
    )
    for bad in ('changes: ""\n', "changes: 3\n"):
        with pytest.raises(ProjectError, match="changes"):
            parse_project(head + bad, tmp_path / "shape.yml")


def test_changes_and_no_changes_conflict(work, capsys):
    root, base, cur = work
    f = both_planned(root)
    rc, _, err = run_diff(capsys, str(base), str(cur), "--changes", str(f), "--no-changes")
    assert rc == 2 and "cannot be combined" in err
