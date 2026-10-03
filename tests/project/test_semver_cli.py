"""W1-13: change classes on the command line, in ``shape.yml``, in the planned-change registry and
in the schema drift gate (items 3 to 6)."""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main
from shape.project import load_project, parse_project, problems, schema
from shape.project.changes import ChangesError, parse_changes
from shape.quality import GateResult, ValidationContext
from shape.quality.gates import SchemaDriftGate
from shape.quality.verifyconfig import VerifyConfig, VerifyConfigError
from shape.schemacheck import validate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
PROJECT = "format: shape-project\nversion: 1\n"
CHANGES = "format: shape-planned-changes\nversion: 1\nchanges:\n"
IN_WINDOW, AFTER = "2026-11-15", "2026-12-01"


def write_csv(path: Path, *, nulls=0, extra=False, drop=False, shift=0.0) -> Path:
    cols = ["id", "status", "amount", "note"]
    if drop:
        cols.remove("status")
    if extra:
        cols.append("tier")
    lines = [",".join(cols)]
    for i in range(200):
        row = {
            "id": str(i),
            "status": ["new", "paid", "paid", "shipped"][i % 4],
            "amount": str(10.0 + ((i * 37) % 200) / 10 + shift),
            "note": "" if i < nulls else f"note {i}",
            "tier": ["gold", "silver"][i % 2],
        }
        lines.append(",".join(row[c] for c in cols))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


@pytest.fixture()
def work(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    def profile(name: str, **kw) -> Path:
        csv = write_csv(tmp_path / f"{name}.csv", **kw)
        out = tmp_path / f"{name}.shape"
        assert main(["profile", str(csv), "-o", str(out), "--no-project"]) == 0
        return out

    def build():
        profile("base")
        profile("removed", drop=True)
        profile("added", extra=True)
        profile("shifted", shift=40.0)
        profile("few_nulls", nulls=4)
        profile("many_nulls", nulls=60)
        capsys.readouterr()

    build()
    return tmp_path


def run_diff(capsys, *args):
    capsys.readouterr()
    rc = main(["diff", "--no-project", *args]) if "--project" not in args else main(["diff", *args])
    cap = capsys.readouterr()
    out = cap.out.strip()
    return rc, (json.loads(out) if out else None), cap.err


# ---- 4. diff output ----------------------------------------------------------------------------


def test_removed_column_is_major_and_fails_on_breaking(work, capsys):
    rc, out, err = run_diff(capsys, "base.shape", "removed.shape", "--fail-on", "breaking")
    assert rc == 1
    assert out["semver"] == {"bump": "major", "breaking": 1, "additive": 0, "cosmetic": 0}
    assert out["fail_on"] == "breaking" and out["failed"] is True
    change = out["changes"][0]
    assert change["kind"] == "column_removed" and change["class"] == "breaking"
    assert change["class_reason"]
    lines = err.strip().splitlines()
    assert "shape: status: column_removed [breaking]" in lines
    assert lines[-1] == "bump: major (1 breaking, 0 additive, 0 cosmetic)"


def test_added_column_is_minor_exit_0_on_breaking_1_on_additive(work, capsys):
    rc, out, err = run_diff(capsys, "base.shape", "added.shape", "--fail-on", "breaking")
    assert rc == 0 and out["semver"]["bump"] == "minor" and out["failed"] is False
    assert err.strip().splitlines()[-1] == "bump: minor (0 breaking, 1 additive, 0 cosmetic)"
    rc, out, _ = run_diff(capsys, "base.shape", "added.shape", "--fail-on", "additive")
    assert rc == 1 and out["failed"] is True
    rc, _, _ = run_diff(capsys, "base.shape", "added.shape", "--fail-on", "cosmetic")
    assert rc == 1


def test_a_mean_shift_alone_is_patch(work, capsys):
    rc, out, err = run_diff(capsys, "base.shape", "shifted.shape", "--fail-on", "additive")
    assert rc == 0 and out["semver"]["bump"] == "patch"
    assert {c["class"] for c in out["changes"]} == {"cosmetic"}
    rc, _, _ = run_diff(capsys, "base.shape", "shifted.shape", "--fail-on", "cosmetic")
    assert rc == 1


def test_no_flag_means_no_failure_and_no_fail_keys(work, capsys):
    rc, out, _ = run_diff(capsys, "base.shape", "removed.shape")
    assert rc == 0 and "failed" not in out and "fail_on" not in out
    rc, out, err = run_diff(capsys, "base.shape", "base.shape", "--fail-on", "cosmetic")
    assert rc == 0 and out["semver"]["bump"] == "none"
    assert err.strip().splitlines()[-1] == "bump: none (0 breaking, 0 additive, 0 cosmetic)"


def test_null_rate_from_zero_is_breaking_and_from_two_percent_is_cosmetic(work, capsys):
    _, out, _ = run_diff(capsys, "base.shape", "many_nulls.shape")
    assert {(c["kind"], c["class"]) for c in out["changes"]} >= {("null_rate_change", "breaking")}
    _, out, _ = run_diff(capsys, "few_nulls.shape", "many_nulls.shape")
    assert {(c["kind"], c["class"]) for c in out["changes"]} >= {("null_rate_change", "cosmetic")}


@pytest.mark.parametrize(
    ("current", "version"),
    [
        ("removed.shape", "2.0.0"),
        ("added.shape", "1.5.0"),
        ("shifted.shape", "1.4.3"),
        ("base.shape", "1.4.2"),
    ],
)
def test_version_from(work, capsys, current, version):
    _, out, err = run_diff(capsys, "base.shape", current, "--version-from", "1.4.2")
    assert out["semver"]["next_version"] == version
    assert f"version: {version}" in err


def test_without_version_from_there_is_no_next_version(work, capsys):
    _, out, _ = run_diff(capsys, "base.shape", "removed.shape")
    assert "next_version" not in out["semver"]


@pytest.mark.parametrize("bad", ["1.4", "v1.4.2", "1.4.2.0", "x"])
def test_bad_version_from_is_an_input_error(work, capsys, bad):
    rc, _, err = run_diff(capsys, "base.shape", "removed.shape", "--version-from", bad)
    assert rc == 2 and "X.Y.Z" in err


def test_unknown_fail_on_class_exits_2(work, capsys):
    with pytest.raises(SystemExit) as exc:
        run_diff(capsys, "base.shape", "removed.shape", "--fail-on", "major")
    assert exc.value.code == 2


def test_fail_on_and_fail_on_drift_may_be_combined(work, capsys):
    rc, _, _ = run_diff(capsys, "base.shape", "shifted.shape", "--fail-on", "breaking")
    assert rc == 0
    rc, _, _ = run_diff(
        capsys, "base.shape", "shifted.shape", "--fail-on", "breaking", "--fail-on-drift"
    )
    assert rc == 1  # either one fails the run
    rc, _, _ = run_diff(
        capsys, "base.shape", "removed.shape", "--fail-on", "breaking", "--fail-on-drift"
    )
    assert rc == 1
    rc, _, _ = run_diff(
        capsys, "base.shape", "base.shape", "--fail-on", "cosmetic", "--fail-on-drift"
    )
    assert rc == 0


# ---- 3. overrides: policy file, planned entries, shape.yml -----------------------------------


def test_policy_file_classes_on_the_command_line(work, capsys):
    (work / "p.json").write_text(
        json.dumps({"classes": {"column_removed": "cosmetic"}}), encoding="utf-8"
    )
    rc, out, _ = run_diff(
        capsys, "base.shape", "removed.shape", "--policy", "p.json", "--fail-on", "breaking"
    )
    assert rc == 0 and out["semver"]["bump"] == "patch"
    (work / "p.json").write_text(
        json.dumps({"column_classes": {"stat*": {"column_removed": "additive"}}}),
        encoding="utf-8",
    )
    _, out, _ = run_diff(capsys, "base.shape", "removed.shape", "--policy", "p.json")
    assert out["changes"][0]["class"] == "additive"


@pytest.mark.parametrize(
    "policy",
    [
        {"classes": {"made_up": "breaking"}},
        {"classes": {"column_removed": "major"}},
        {"column_classes": {"status": {"column_removed": "high"}}},
    ],
)
def test_unknown_kind_or_class_in_a_policy_file_exits_2(work, capsys, policy):
    (work / "p.json").write_text(json.dumps(policy), encoding="utf-8")
    rc, _, err = run_diff(capsys, "base.shape", "removed.shape", "--policy", "p.json")
    assert rc == 2 and "shape: error" in err


def entry(**kw) -> str:
    base = {
        "id": "e1",
        "column": "status",
        "kinds": "[column_removed]",
        "from": "2026-11-01",
        "until": "2026-11-30",
        "reason": "release",
    }
    base.update(kw)
    lines = [
        f"  - {k}: {v}" if i == 0 else f"    {k}: {v}" for i, (k, v) in enumerate(base.items())
    ]
    return "\n".join(lines) + "\n"


def planned(work, **kw) -> str:
    f = work / "c.yml"
    f.write_text(CHANGES + entry(**kw), encoding="utf-8")
    return str(f)


def test_planned_expect_for_a_removed_column_exits_0_then_1_after_until(work, capsys):
    f = planned(work)
    args = ("base.shape", "removed.shape", "--fail-on", "breaking", "--changes", f)
    rc, out, err = run_diff(capsys, *args, "--on", IN_WINDOW)
    assert rc == 0 and out["failed"] is False
    assert out["semver"] == {
        "bump": "none",
        "breaking": 0,
        "additive": 0,
        "cosmetic": 0,
        "planned": {"breaking": 1, "additive": 0, "cosmetic": 0},
    }
    assert out["changes"][0]["class"] == "breaking" and out["changes"][0]["planned"]
    assert "(planned: e1)" in err and "[breaking]" in err
    rc, out, _ = run_diff(capsys, *args, "--on", AFTER)  # one day after `until`
    assert rc == 1 and out["semver"]["bump"] == "major" and out["semver"]["breaking"] == 1
    assert "planned" not in out["changes"][0]


def test_planned_entry_with_class_cosmetic_changes_the_class(work, capsys):
    f = planned(work, **{"class": "cosmetic"})
    _, out, _ = run_diff(capsys, "base.shape", "removed.shape", "--changes", f, "--on", IN_WINDOW)
    change = out["changes"][0]
    assert change["class"] == "cosmetic" and "planned change" in change["class_reason"]
    assert out["semver"]["planned"] == {"breaking": 0, "additive": 0, "cosmetic": 1}


def test_severity_entry_counts_at_its_class(work, capsys):
    f = planned(work, action="severity", severity="high", **{"class": "cosmetic"})
    args = ("base.shape", "removed.shape", "--changes", f, "--on", IN_WINDOW)
    rc, out, _ = run_diff(capsys, *args, "--fail-on", "breaking")
    assert rc == 0 and out["semver"]["cosmetic"] == 1 and out["semver"]["bump"] == "patch"
    rc, _, _ = run_diff(capsys, *args, "--fail-on", "cosmetic")
    assert rc == 1
    f = planned(work, action="severity", severity="high")  # its own class: breaking
    rc, out, _ = run_diff(
        capsys, "base.shape", "removed.shape", "--changes", f, "--on", IN_WINDOW,
        "--fail-on", "breaking",
    )  # fmt: skip
    assert rc == 1 and out["semver"]["breaking"] == 1


def test_suppressed_entries_are_not_counted_or_reported(work, capsys):
    f = planned(work, action="suppress")
    rc, out, _ = run_diff(
        capsys, "base.shape", "removed.shape", "--changes", f, "--on", IN_WINDOW,
        "--fail-on", "cosmetic",
    )  # fmt: skip
    assert rc == 0 and out["changes"] == [] and out["semver"]["bump"] == "none"


def test_planned_class_in_python_and_errors():
    plan = parse_changes(CHANGES + entry(**{"class": "additive"}), "c.yml")
    assert plan.entries[0].class_ == "additive"
    assert plan.entries[0].to_dict()["class"] == "additive"
    assert "class" not in parse_changes(CHANGES + entry(), "c.yml").entries[0].to_dict()
    for bad in ("major", "high", "''"):
        with pytest.raises(ChangesError):
            parse_changes(CHANGES + entry(**{"class": bad}), "c.yml")


def test_a_planned_file_without_class_reads_as_before():
    for path in sorted((FIXTURES / "planned_changes").glob("v*/shape-changes.yml")):
        from shape.project.changes import load_changes

        plan = load_changes(path)
        assert all(e.class_ is None for e in plan.entries)
        assert all("class" not in e.to_dict() for e in plan.entries)
        assert (
            validate(plan.document, __import__("shape.project.changes", fromlist=["x"]).schema())
            == []
        )


def test_planned_changes_schema_has_an_optional_class():
    from shape.project.changes import schema as changes_schema

    change = changes_schema()["$defs"]["change"]
    assert change["properties"]["class"]["enum"] == ["breaking", "additive", "cosmetic"]
    assert "class" not in change["required"]


def write_project(root: Path, body: str) -> Path:
    f = root / "shape.yml"
    f.write_text(PROJECT + body, encoding="utf-8")
    return f


def test_source_classes_in_shape_yml_apply_and_a_policy_file_replaces_them(work, capsys):
    write_project(
        work,
        "sources:\n  orders:\n    path: data\n    classes:\n      column_removed: cosmetic\n",
    )
    rc, out, _ = run_diff(
        capsys, "--project", "shape.yml", "base.shape", "removed.shape", "--fail-on", "breaking"
    )
    assert rc == 0 and out["changes"][0]["class"] == "cosmetic"
    (work / "p.json").write_text(
        "{}", encoding="utf-8"
    )  # an explicit policy replaces the project's
    rc, out, _ = run_diff(
        capsys, "--project", "shape.yml", "base.shape", "removed.shape", "--policy", "p.json"
    )
    assert out["changes"][0]["class"] == "breaking"


# ---- the project file: format, validation, compatibility ---------------------------------------


def test_source_classes_are_validated_with_their_key_path():
    ok = parse_project(
        PROJECT + "sources:\n  s:\n    path: p\n    classes:\n      dtype_widening: additive\n",
        "shape.yml",
    )
    assert ok.source("s").classes == {"dtype_widening": "additive"}
    assert ok.source("s").drift_policy()["classes"] == {"dtype_widening": "additive"}
    bad_class = problems(
        {"format": "shape-project", "version": 1,
         "sources": {"s": {"path": "p", "classes": {"column_added": "major"}}}}
    )  # fmt: skip
    assert any("sources.s.classes.column_added" in p for p in bad_class)
    bad_kind = problems(
        {"format": "shape-project", "version": 1,
         "sources": {"s": {"path": "p", "classes": {"made_up": "breaking"}}}}
    )  # fmt: skip
    assert any("made_up" in p and "sources.s.classes" in p for p in bad_kind)


def test_fail_on_is_valid_for_schema_drift_only():
    doc = {"format": "shape-project", "version": 1, "sources": {"s": {"path": "p"}}}
    ok = {**doc, "gates": {"schema_drift": {"mode": "enforce", "fail_on": "additive"}}}
    assert problems(ok) == []
    project = parse_project(
        PROJECT
        + "sources:\n  s:\n    path: p\ngates:\n  schema_drift:\n    mode: enforce\n"
        + "    fail_on: additive\n  file_format:\n    mode: observe\n",
        "shape.yml",
    )
    assert project.gate_fail_on("schema_drift") == "additive"
    assert project.gate_fail_on("file_format") is None
    for gate in (
        "file_format",
        "range_constraint",
        "temporal_consistency",
        "schema_conformance",
        "null_constraint",
        "unique_constraint",
        "referential_integrity",
        "distribution",
    ):
        found = problems({**doc, "gates": {gate: {"mode": "enforce", "fail_on": "breaking"}}})
        assert any(f"gates.{gate}.fail_on" in p and gate in p for p in found), (gate, found)
    found = problems({**doc, "gates": {"schema_drift": {"mode": "enforce", "fail_on": "high"}}})
    assert any("gates.schema_drift.fail_on" in p for p in found)


def test_project_schema_lists_every_change_kind():
    from shape.drift.semver import CLASSES, DEFAULT_CLASSES, PSEUDO_KINDS

    classes = schema()["$defs"]["classes"]
    assert set(classes["properties"]) == set(DEFAULT_CLASSES) | set(PSEUDO_KINDS)
    assert all(p["enum"] == list(CLASSES) for p in classes["properties"].values())
    gate = schema()["$defs"]["gate"]["properties"]["fail_on"]
    assert gate["enum"] == list(CLASSES)
    assert "classes" in schema()["$defs"]["source"]["properties"]
    assert "fail_on" not in schema()["$defs"]["gate"].get("required", [])


def test_a_version_1_project_written_before_this_change_reads_the_same():
    project = load_project(FIXTURES / "project" / "v1" / "shape.yml")
    assert project.version == 1 and project.fail_on == {}
    assert all(dict(s.classes) == {} for s in project.sources.values())
    assert all("classes" not in s.drift_policy() for s in project.sources.values())
    assert all(project.gate_fail_on(g) is None for g in project.gates)
    assert problems(project.document) == []


# ---- 6. the schema drift gate ------------------------------------------------------------------


def ctx(tables, baseline, **config):
    return ValidationContext(tables=tables, config={"baseline": baseline, **config})


T1 = {"t": pa.table({"a": pa.array([1], pa.int32()), "b": ["x"]})}
BASE1 = {"t": {"columns": {"a": "int32", "b": "str"}}}


def test_gate_defaults_are_as_strict_as_before():
    removed = SchemaDriftGate().check(
        ctx(T1, {"t": {"columns": {"a": "int32", "b": "str", "c": "str"}}})
    )
    assert not removed.passed and removed.errors == ["Table 't': column 'c' removed"]
    added = SchemaDriftGate().check(ctx(T1, {"t": {"columns": {"a": "int32"}}}))
    assert added.passed and added.warnings == ["Table 't': new column 'b'"]
    assert added.details == {
        "additive": ["Table 't': new column 'b'"],
        "breaking": [],
        "cosmetic": [],
        "fail_on": "breaking",
    }
    narrowed = SchemaDriftGate().check(ctx(T1, {"t": {"columns": {"a": "int64", "b": "str"}}}))
    assert not narrowed.passed  # int64 to int32 is a type change
    widened = SchemaDriftGate().check(ctx(T1, {"t": {"columns": {"a": "int16", "b": "str"}}}))
    assert not widened.passed  # int16 to int32 is a widening, and breaking by default


def test_gate_fail_on_additive_and_cosmetic():
    only_added = {"t": {"columns": {"a": "int32"}}}
    assert SchemaDriftGate().check(ctx(T1, only_added, fail_on="breaking")).passed
    r = SchemaDriftGate().check(ctx(T1, only_added, fail_on="additive"))
    assert not r.passed and r.errors == ["Table 't': new column 'b'"] and r.warnings == []
    assert r.details["fail_on"] == "additive"
    r = SchemaDriftGate().check(ctx(T1, BASE1, fail_on="cosmetic"))
    assert r.passed and r.details["breaking"] == r.details["additive"] == []


def test_gate_cosmetic_changes_come_from_class_overrides():
    only_added = {"t": {"columns": {"a": "int32"}}}
    classes = {"column_added": "cosmetic"}
    r = SchemaDriftGate().check(ctx(T1, only_added, classes=classes))
    assert r.passed and r.details["cosmetic"] == ["Table 't': new column 'b'"]
    assert r.details["additive"] == []
    r = SchemaDriftGate().check(ctx(T1, only_added, classes=classes, fail_on="cosmetic"))
    assert not r.passed
    cols = {"b": {"column_added": "breaking"}}
    r = SchemaDriftGate().check(ctx(T1, only_added, column_classes=cols))
    assert not r.passed and r.details["breaking"] == ["Table 't': new column 'b'"]
    r = SchemaDriftGate().check(
        ctx(
            T1,
            only_added,
            classes={"column_added": "breaking"},
            column_classes={"t.b": {"column_added": "additive"}},
        )
    )
    assert r.passed  # the most specific pattern wins


def test_gate_widening_is_breaking_unless_the_policy_says_additive():
    narrow = {"t": {"columns": {"a": "int16", "b": "str"}}}  # int16 -> int32: widening
    wider = {"dtype_widening": "additive"}
    r = SchemaDriftGate().check(ctx(T1, narrow, classes=wider))
    assert r.passed and len(r.details["additive"]) == 1 and r.details["breaking"] == []
    assert not SchemaDriftGate().check(ctx(T1, narrow, classes=wider, fail_on="additive")).passed
    shrunk = {"t": {"columns": {"a": "int64", "b": "str"}}}  # int64 -> int32: not a widening
    assert not SchemaDriftGate().check(ctx(T1, shrunk, classes=wider)).passed
    assert not SchemaDriftGate().check(ctx(T1, shrunk)).passed
    f32 = {"t": pa.table({"x": pa.array([1.0], pa.float64())})}
    f = {"t": {"columns": {"x": "float32"}}}
    assert SchemaDriftGate().check(ctx(f32, f, classes=wider)).passed
    assert not SchemaDriftGate().check(ctx(f32, f)).passed  # breaking without the policy


def test_gate_rejects_an_unknown_fail_on_or_class():
    with pytest.raises(ValueError, match="fail_on"):
        SchemaDriftGate().check(ctx(T1, BASE1, fail_on="major"))
    with pytest.raises(ValueError, match="made_up"):
        SchemaDriftGate().check(ctx(T1, BASE1, classes={"made_up": "breaking"}))


def test_gate_planned_entries_and_their_class():
    plan = parse_changes(CHANGES + entry(id="n", column="b", kinds="[column_removed]"), "c.yml")
    base = {"t": {"columns": {"a": "int32", "b": "str", "c": "str"}}}
    applier = plan.applier("2026-11-15", None)
    c = ValidationContext(tables=T1, config={"baseline": base}, planned=applier)
    r = SchemaDriftGate().check(c)
    assert not r.passed  # 'c' is not planned
    plan = parse_changes(CHANGES + entry(id="n", column="c", kinds="[column_removed]"), "c.yml")
    c = ValidationContext(
        tables=T1, config={"baseline": base}, planned=plan.applier("2026-11-15", None)
    )
    r = SchemaDriftGate().check(c)
    assert r.passed and r.warnings == ["Table 't': column 'c' removed (planned: n)"]
    assert r.details["breaking"] == ["Table 't': column 'c' removed (planned: n)"]
    # the entry's class decides how the planned change is listed
    plan = parse_changes(
        CHANGES + entry(id="n", column="c", kinds="[column_removed]", **{"class": "cosmetic"}),
        "c.yml",
    )
    c = ValidationContext(
        tables=T1, config={"baseline": base}, planned=plan.applier("2026-11-15", None)
    )
    r = SchemaDriftGate().check(c)
    assert r.details["cosmetic"] and r.details["breaking"] == []
    # one day after until the entry stops matching
    c = ValidationContext(tables=T1, config={"baseline": base}, planned=plan.applier(AFTER, None))
    assert not SchemaDriftGate().check(c).passed
    # a severity entry still fails at high, as before
    plan = parse_changes(
        CHANGES
        + entry(id="n", column="c", kinds="[column_removed]", action="severity", severity="high"),
        "c.yml",
    )
    c = ValidationContext(
        tables=T1, config={"baseline": base}, planned=plan.applier("2026-11-15", None)
    )
    assert not SchemaDriftGate().check(c).passed


def test_no_baseline_gate_still_passes_with_its_warning():
    r = SchemaDriftGate().check(ValidationContext(tables=T1, config={}))
    assert isinstance(r, GateResult) and r.passed
    assert r.warnings == ["No baseline schema configured — nothing to check"]


def test_verify_config_takes_fail_on_and_classes():
    base = {"format": "shape-verify-config", "version": 1, "baseline": BASE1}
    ok = VerifyConfig.from_dict(
        {**base, "fail_on": "additive", "classes": {"dtype_widening": "additive"},
         "column_classes": {"a": {"dtype_change": "cosmetic"}}}
    )  # fmt: skip
    assert ok.rules["fail_on"] == "additive"
    for bad in (
        {"fail_on": "high"},
        {"classes": {"made_up": "breaking"}},
        {"classes": {"column_added": "major"}},
        {"column_classes": {"a": {"column_added": "x"}}},
        {"column_classes": ["a"]},
    ):
        with pytest.raises(VerifyConfigError):
            VerifyConfig.from_dict({**base, **bad})
    # a configuration written before this change reads as before
    old = VerifyConfig.from_dict(base)
    assert "fail_on" not in old.rules and "classes" not in old.rules


# ---- shape verify: shape.yml gates and the report -----------------------------------------------


@pytest.fixture()
def lake(work):
    d = work / "lake"
    d.mkdir()
    pq.write_table(
        pa.table({"id": pa.array([1, 2], pa.int64()), "extra": ["a", "b"]}), d / "o.parquet"
    )
    (work / "verify.json").write_text(
        json.dumps(
            {
                "format": "shape-verify-config",
                "version": 1,
                "baseline": {"o": {"columns": {"id": "int64"}}},
            }
        ),
        encoding="utf-8",
    )
    return work


def run_verify(capsys, *args):
    capsys.readouterr()
    rc = main(["verify", "lake", "--config", "verify.json", *args])
    cap = capsys.readouterr()
    return rc, cap.out, cap.err


def test_verify_added_column_passes_by_default_and_fails_with_fail_on_additive(lake, capsys):
    rc, out, _ = run_verify(capsys, "--no-project", "-o", "r.json")
    assert rc == 0
    report = json.loads((lake / "r.json").read_text())
    gate = next(g for g in report["gates"] if g["gate"] == "schema_drift")
    assert gate["fail_on"] == "breaking"
    assert gate["details"]["additive"] and gate["details"]["breaking"] == []
    write_project(
        lake,
        "sources:\n  o:\n    path: lake\ngates:\n  schema_drift:\n    mode: enforce\n"
        "    fail_on: additive\n",
    )
    rc, out, err = run_verify(capsys, "-o", "r.json")
    assert rc == 1 and "FAIL" in out and "extra" in err
    report = json.loads((lake / "r.json").read_text())
    gate = next(g for g in report["gates"] if g["gate"] == "schema_drift")
    assert gate["fail_on"] == "additive" and gate["mode"] == "enforce"
    other = [g for g in report["gates"] if g["gate"] != "schema_drift"]
    assert all(g["fail_on"] is None for g in other)


def test_verify_config_file_beats_shape_yml_and_source_classes_apply(lake, capsys):
    write_project(
        lake,
        "sources:\n  o:\n    path: lake\n    classes:\n      column_added: breaking\n"
        "gates:\n  schema_drift:\n    mode: enforce\n",
    )
    rc, _, err = run_verify(capsys)
    assert rc == 1 and "extra" in err  # the source says a new column is breaking
    cfg = json.loads((lake / "verify.json").read_text())
    cfg["classes"] = {"column_added": "additive"}
    (lake / "verify.json").write_text(json.dumps(cfg), encoding="utf-8")
    rc, _, _ = run_verify(capsys)
    assert rc == 0  # the configuration file is explicit and wins


def test_project_validate_rejects_fail_on_on_other_gates_naming_the_gate(work, capsys):
    f = write_project(
        work,
        "sources:\n  o:\n    path: lake\ngates:\n  file_format:\n    mode: enforce\n"
        "    fail_on: breaking\n",
    )
    capsys.readouterr()
    rc = main(["project", "validate", str(f)])
    assert rc == 2
    assert "file_format" in capsys.readouterr().err
    f = write_project(
        work,
        "sources:\n  o:\n    path: lake\ngates:\n  schema_drift:\n    mode: observe\n"
        "    fail_on: cosmetic\n",
    )
    assert main(["project", "validate", str(f)]) == 0


def test_shape_diff_function_is_the_same_engine_as_the_cli(work):
    base, removed = shape.load("base.shape"), shape.load("removed.shape")
    r = shape.diff(base, removed, fail_on="breaking")
    assert r.failed and r.semver["bump"] == "major"
