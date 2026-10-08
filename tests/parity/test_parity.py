"""W3-13 deliverables 1 and 2: ``shape parity A B`` and the parity report."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import shape
from shape.cli.main import main
from shape.drift.engine import resolve_policy
from shape.parity import (
    CATEGORIES,
    ParityInputError,
    build_report,
    compare,
    load_side,
    render_text,
)
from shape.privacy.safe_profile import to_safe_profile


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    rc = main(["parity", *argv])
    out = capsys.readouterr()
    return rc, out.out, out.err


def report_of(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    rc, out, _ = run(capsys, *argv, "--json")
    return rc, json.loads(out)


def failures(report: dict[str, Any], category: str | None = None) -> list[dict[str, Any]]:
    return [
        c
        for c in report["checks"]
        if c["status"] == "fail" and (category is None or c["category"] == category)
    ]


def status_of(report: dict[str, Any], category: str, table: str, column: str | None) -> str:
    (found,) = [
        c
        for c in report["checks"]
        if (c["category"], c["table"], c["column"]) == (category, table, column)
    ]
    return str(found["status"])


# ---- known answers: the same shape passes, a damaged environment fails ----------------------


def test_identical_environments_have_parity(env, capsys):
    a, b = env("prod"), env("dev")
    rc, rep = report_of(capsys, str(a), str(b), "--dataset")
    assert rc == 0
    assert rep["parity"] is True
    assert rep["summary"]["fail"] == 0
    assert {c["category"] for c in rep["checks"]} == set(CATEGORIES)


def test_a_fresh_sample_of_the_same_data_has_parity(env, capsys):
    """Another random draw of the same distributions is not drift."""
    rc, rep = report_of(capsys, str(env("prod", seed=1)), str(env("dev", seed=2)), "--dataset")
    assert rc == 0, failures(rep)


def test_dropped_column_fails_naming_table_and_column(env, capsys):
    rc, rep = report_of(
        capsys, str(env("prod")), str(env("dev", drop=("orders", "note"))), "--dataset"
    )
    assert rc == 1
    (f,) = failures(rep, "columns")
    assert (f["table"], f["column"]) == ("orders", "note")
    assert f["details"]["reason"] == "missing on B"


def test_extra_column_on_b_also_fails(env, capsys):
    rc, rep = report_of(capsys, str(env("prod")), str(env("dev", extra_column=True)), "--dataset")
    assert rc == 1
    (f,) = failures(rep, "columns")
    assert (f["table"], f["column"], f["details"]["reason"]) == ("orders", "channel", "only on B")


def test_changed_type_fails_with_both_types(env, capsys):
    rc, rep = report_of(capsys, str(env("prod")), str(env("dev", amount_as_text=True)), "--dataset")
    assert rc == 1
    (f,) = failures(rep, "types")
    assert (f["table"], f["column"]) == ("orders", "amount")
    assert f["details"] == {"a": "float", "b": "string"}
    # a column whose type differs has no comparable distribution
    assert status_of(rep, "distributions", "orders", "amount") == "not_measured"


def test_removed_foreign_key_fails_naming_table_and_column(env, capsys):
    rc, rep = report_of(capsys, str(env("prod")), str(env("dev", orphan_fk=True)), "--dataset")
    assert rc == 1
    (f,) = failures(rep, "relationships")
    assert (f["table"], f["column"]) == ("orders", "customer_id")
    assert f["details"]["parent"] == "customer"
    assert f["details"]["reason"] == "not detected on B"


def test_missing_table_and_extra_table_fail(env, capsys):
    rc, rep = report_of(capsys, str(env("prod")), str(env("dev", skip_table="orders")), "--dataset")
    assert rc == 1
    tables = {(c["table"], c["details"].get("reason")) for c in failures(rep, "tables")}
    assert tables == {("orders", "missing on B")}
    rc, rep = report_of(capsys, str(env("prod2")), str(env("dev2", extra_table=True)), "--dataset")
    assert rc == 1
    tables = {(c["table"], c["details"].get("reason")) for c in failures(rep, "tables")}
    assert tables == {("audit", "only on B")}


def test_key_lost_on_b_fails(env, capsys):
    """B repeats an order id, so no primary key is detected on B."""
    a = env("prod")
    b = env("dev")
    import pyarrow.parquet as pq

    t = pq.read_table(b / "orders.parquet").to_pandas()
    t.loc[1, "order_id"] = t.loc[0, "order_id"]
    t.to_parquet(b / "orders.parquet")
    rc, rep = report_of(capsys, str(a), str(b), "--dataset")
    assert rc == 1
    (f,) = failures(rep, "keys")
    assert (f["table"], f["column"]) == ("orders", "order_id")
    assert f["details"]["a"] == ["order_id"]


# ---- null rates and distributions: thresholds and their boundary ----------------------------


def test_null_rate_boundary_follows_the_default_threshold(env, capsys):
    """The default ``null_rate`` threshold is 0.05: exactly 0.05 passes, 0.06 fails."""
    a = env("prod")
    rc, rep = report_of(capsys, str(a), str(env("at", note_null=0.05)), "--dataset")
    assert rc == 0, failures(rep)
    assert status_of(rep, "nulls", "orders", "note") == "pass"
    rc, rep = report_of(capsys, str(a), str(env("over", note_null=0.06)), "--dataset")
    assert rc == 1
    (f,) = failures(rep, "nulls")
    assert (f["table"], f["column"]) == ("orders", "note")
    assert f["details"]["a"] == 0.0 and f["details"]["b"] == 0.06
    assert f["details"]["threshold"] == 0.05


def test_distribution_shift_fails(env, capsys):
    rc, rep = report_of(capsys, str(env("prod")), str(env("dev", amount_scale=3.0)), "--dataset")
    assert rc == 1
    (f,) = [c for c in failures(rep, "distributions") if c["column"] == "amount"]
    assert {c["kind"] for c in f["details"]["changes"]} >= {"mean_shift"}


def test_category_mix_shift_fails(env, capsys):
    rc, rep = report_of(
        capsys, str(env("prod")), str(env("dev", status_p=(0.6, 0.2, 0.2))), "--dataset"
    )
    assert rc == 1
    (f,) = [c for c in failures(rep, "distributions") if c["column"] == "status"]
    assert {c["kind"] for c in f["details"]["changes"]} == {"category_shift"}


def test_ids_that_changed_between_loads_are_not_a_distribution_failure(env, capsys):
    """Another load brings other order ids and customer ids: new values in a key column."""
    rc, rep = report_of(
        capsys, str(env("prod", seed=1)), str(env("dev", seed=5, orders=1700)), "--dataset"
    )
    assert not failures(rep, "distributions"), failures(rep)


def test_project_thresholds_override_defaults_and_name_owners(env, tmp_path, capsys, monkeypatch):
    (tmp_path / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nname: p\n"
        "sources:\n  prod:\n    path: data/prod\n    thresholds:\n      null_rate: 0.5\n"
        "    columns:\n      orders.note:\n        owner: sales-data@example.com\n"
        "      amount:\n        owner: finance@example.com\n",
        encoding="utf-8",
    )
    a, b = env("prod"), env("dev", note_null=0.3, amount_scale=3.0)
    monkeypatch.chdir(tmp_path)
    rc, rep = report_of(capsys, str(a), str(b), "--dataset", "--source", "prod")
    # null_rate threshold 0.5 now tolerates the 0.3 difference ...
    assert status_of(rep, "nulls", "orders", "note") == "pass"
    # ... and the owner of a failing column is named
    (f,) = [c for c in failures(rep, "distributions") if c["column"] == "amount"]
    assert f["owner"] == "finance@example.com"
    assert rep["options"]["source"] == "prod"
    assert rc == 1
    # the note column carries its owner on a passing check, too
    note = [c for c in rep["checks"] if c["column"] == "note" and c["category"] == "nulls"]
    assert note[0]["owner"] == "sales-data@example.com"


def test_ignored_columns_are_left_out(env, tmp_path, capsys, monkeypatch):
    (tmp_path / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nname: p\n"
        "sources:\n  prod:\n    path: data/prod\n    ignore: [note]\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    rc, rep = report_of(
        capsys, str(env("prod")), str(env("dev", drop=("orders", "note"))), "--dataset"
    )
    assert rc == 0, failures(rep)
    assert not [c for c in rep["checks"] if c["column"] == "note"]


# ---- row counts: absolute, scaled, boundaries -------------------------------------------------


def test_row_tolerance_boundary(env, capsys):
    """Absolute mode: the difference is relative to A, and exactly the tolerance passes."""
    a = env("prod", customers=1000, orders=1000)
    rc, rep = report_of(capsys, str(a), str(env("ok", customers=1000, orders=1100)), "--dataset")
    assert status_of(rep, "row_counts", "orders", None) == "pass"
    rc, rep = report_of(capsys, str(a), str(env("no", customers=1000, orders=1101)), "--dataset")
    assert status_of(rep, "row_counts", "orders", None) == "fail"
    assert rc == 1
    rc, rep = report_of(
        capsys,
        str(a),
        str(env("wide", customers=1000, orders=1101)),
        "--dataset",
        "--row-tolerance",
        "0.2",
    )
    assert status_of(rep, "row_counts", "orders", None) == "pass"


def test_row_tolerance_zero_requires_equal_counts(env, capsys):
    a = env("prod")
    rc, _ = report_of(capsys, str(a), str(env("same")), "--dataset", "--row-tolerance", "0")
    assert rc == 0
    rc, _ = report_of(
        capsys, str(a), str(env("more", orders=1601)), "--dataset", "--row-tolerance", "0"
    )
    assert rc == 1


def test_a_smaller_environment_with_the_same_proportions_needs_scaled(env, capsys):
    prod = env("prod", customers=2000, orders=8000)
    dev = env("dev", customers=200, orders=800)
    rc, rep = report_of(capsys, str(prod), str(dev), "--dataset")
    assert rc == 1 and failures(rep, "row_counts")
    rc, rep = report_of(capsys, str(prod), str(dev), "--dataset", "--scaled")
    assert rc == 0, failures(rep)
    rows = {c["table"]: c["details"] for c in rep["checks"] if c["category"] == "row_counts"}
    assert rows["orders"]["mode"] == "scaled"
    assert rows["orders"]["share_a"] == pytest.approx(0.8)


def test_scaled_fails_when_the_proportions_differ(env, capsys):
    prod = env("prod", customers=1000, orders=4000)
    dev = env("dev", customers=1000, orders=1000)  # orders are 50% of prod's share, not 80%
    rc, rep = report_of(capsys, str(prod), str(dev), "--dataset", "--scaled")
    assert rc == 1
    assert {c["table"] for c in failures(rep, "row_counts")} == {"customer", "orders"}


def test_scaled_boundary_is_the_difference_of_shares(env, capsys):
    """Shares 0.8 and 0.7 differ by 0.1: at the tolerance passes, below it fails."""
    prod = env("prod", customers=1000, orders=4000)
    dev = env("dev", customers=1000, orders=2333)  # share 0.7001
    rc, _ = report_of(
        capsys, str(prod), str(dev), "--dataset", "--scaled", "--row-tolerance", "0.11"
    )
    assert rc == 0
    rc, _ = report_of(
        capsys, str(prod), str(dev), "--dataset", "--scaled", "--row-tolerance", "0.09"
    )
    assert rc == 1


def test_an_empty_table_on_a_nonempty_one(env, tmp_path, capsys):
    """A has rows, B has none: fails in both modes, never a division by zero."""
    prod = env("prod")
    dev = env("dev")
    import pyarrow.parquet as pq

    t = pq.read_table(dev / "orders.parquet")
    pq.write_table(t.slice(0, 0), dev / "orders.parquet")
    rc, rep = report_of(capsys, str(prod), str(dev), "--dataset")
    assert rc == 1
    assert status_of(rep, "row_counts", "orders", None) == "fail"


# ---- known answer on a generated domain -------------------------------------------------------


def test_generated_domain_small_and_medium(tmp_path, capsys):
    """The retail domain at ``small`` and ``medium``: same proportions, 90 times the rows."""
    from shape.generation.domains import load_domain
    from shape.generation.engine import Engine

    paths = {}
    for scale in ("small", "medium"):
        out = tmp_path / scale
        out.mkdir()
        tables = Engine(load_domain("retail").schema, scale=scale, seed=7).generate().tables
        import pyarrow.parquet as pq

        for name, table in tables.items():
            pq.write_table(table, out / f"{name}.parquet")
        paths[scale] = out
    small, medium = str(paths["small"]), str(paths["medium"])
    rc, rep = report_of(capsys, small, medium, "--dataset")
    assert rc == 1 and failures(rep, "row_counts")
    rc, rep = report_of(capsys, small, medium, "--dataset", "--scaled")
    assert rc == 0, failures(rep)
    assert rep["parity"] is True
    assert status_of(rep, "distributions", "customer", "customer_id") == "not_measured"


# ---- inputs: files, profiles, exports, safe profiles -----------------------------------------


def test_single_files_compare_whatever_they_are_called(env, tmp_path, capsys):
    a = env("prod") / "orders.parquet"
    b = env("dev", seed=2) / "orders.parquet"
    c = tmp_path / "renamed_orders.parquet"
    c.write_bytes(b.read_bytes())
    rc, rep = report_of(capsys, str(a), str(c))
    assert rc == 0, failures(rep)
    assert {t for t in (c["table"] for c in rep["checks"])} == {"orders"}


def test_profile_inputs_and_content_ids(env, tmp_path, capsys):
    a, b = env("prod"), env("dev")
    pa, pb = tmp_path / "a.shape", tmp_path / "b.shape"
    # full captures: the profile parity computes from data is the whole profile (a safe capture,
    # W1-11's default, is a redacted body with another content id)
    cid_a = shape.save(shape.profile(_tables(a)), str(pa), capture="full")
    shape.save(shape.profile(_tables(b)), str(pb), capture="full")
    rc, rep = report_of(capsys, str(pa), str(pb))
    assert rc == 0
    assert rep["inputs"]["a"]["kind"] == "profile"
    assert rep["inputs"]["a"]["content_id"] == cid_a
    # data and its profile are the same thing to parity, and have the same content id
    rc, rep = report_of(capsys, str(a), str(pb), "--dataset")
    assert rep["inputs"]["a"]["kind"] == "data"
    assert rep["inputs"]["a"]["content_id"] == cid_a
    assert rc == 0


def _tables(folder: Path) -> dict[str, str]:
    return {p.stem: str(p) for p in sorted(folder.glob("*.parquet"))}


def test_exported_profile_is_an_input(env, tmp_path, capsys):
    prof = shape.profile(_tables(env("prod")))
    pa = tmp_path / "a.shape"
    shape.save(prof, str(pa))
    assert main(["profile", "export", str(pa), "-o", str(tmp_path / "a.json")]) == 0
    capsys.readouterr()
    rc, rep = report_of(capsys, str(tmp_path / "a.json"), str(env("dev")), "--dataset")
    assert rc == 0, failures(rep)
    assert rep["inputs"]["a"]["kind"] == "profile-export"
    assert len(rep["inputs"]["a"]["content_id"]) == 64


def test_safe_profile_side_reports_not_measured_for_what_it_lacks(env, tmp_path, capsys):
    """A safe profile holds no top values and only folded categories; keys and relationships it
    does hold. What it cannot support is ``not_measured``, never a pass."""
    prod = env("prod")
    safe = to_safe_profile(shape.profile(_tables(prod)))
    safe_path = tmp_path / "prod.safe.json"
    safe.save(safe_path)
    # strip the distribution evidence of one column, as a stricter policy would
    doc = json.loads(safe_path.read_text())
    for key in ("quantiles", "mean", "std", "bounds", "distribution", "distribution_params"):
        doc["tables"]["orders"]["columns"]["amount"][key] = None
    safe_path.write_text(json.dumps(doc))
    rc, rep = report_of(capsys, str(safe_path), str(env("dev", seed=2)), "--dataset")
    assert rep["inputs"]["a"]["kind"] == "safe-profile"
    assert rc == 0, failures(rep)
    assert status_of(rep, "distributions", "orders", "amount") == "not_measured"
    assert rep["summary"]["not_measured"] >= 1
    # what the safe profile does hold is still compared
    assert status_of(rep, "keys", "orders", "order_id") == "pass"
    assert status_of(rep, "relationships", "orders", "customer_id") == "pass"
    assert status_of(rep, "types", "orders", "amount") == "pass"
    # and a damaged environment still fails against it
    rc, rep = report_of(
        capsys,
        str(safe_path),
        str(env("bad", drop=("orders", "note"), orphan_fk=True)),
        "--dataset",
    )
    assert rc == 1
    assert {c["category"] for c in failures(rep)} >= {"columns", "relationships"}


def test_safe_profile_distributions_are_compared_when_present(env, tmp_path, capsys):
    safe_path = tmp_path / "prod.safe.json"
    to_safe_profile(shape.profile(_tables(env("prod")))).save(safe_path)
    rc, rep = report_of(capsys, str(safe_path), str(env("dev", seed=2)), "--dataset")
    assert rc == 0, failures(rep)
    assert status_of(rep, "distributions", "orders", "status") == "pass"
    rc, rep = report_of(
        capsys, str(safe_path), str(env("shifted", status_p=(0.7, 0.2, 0.1))), "--dataset"
    )
    assert rc == 1
    assert any(c["column"] == "status" for c in failures(rep, "distributions"))


def test_hashing_matches_the_safe_profile():
    from shape.parity.checks import _hash_label
    from shape.privacy.safe_profile import _hash_key

    assert _hash_label("12345") == _hash_key("12345")


def test_two_safe_profiles_compare(env, tmp_path, capsys):
    paths = []
    for name, knobs in (("a", {}), ("b", {"drop": ("orders", "note")})):
        path = tmp_path / f"{name}.json"
        to_safe_profile(shape.profile(_tables(env(name, **knobs)))).save(path)
        paths.append(str(path))
    rc, rep = report_of(capsys, *paths)
    assert rc == 1
    (f,) = failures(rep, "columns")
    assert f["column"] == "note"


# ---- the report -----------------------------------------------------------------------------


def test_report_layout(env, tmp_path, capsys):
    out = tmp_path / "report.json"
    rc, text, _ = run(
        capsys, str(env("prod")), str(env("dev")), "--dataset", "--tables", "orders", "-o", str(out)
    )
    assert rc == 0
    rep = json.loads(out.read_text())
    assert rep["format"] == "shape-parity-report"
    assert rep["version"] == 1 and isinstance(rep["version"], int)
    assert set(rep["inputs"]) == {"a", "b"}
    for side in rep["inputs"].values():
        assert {"kind", "content_id", "path"} <= set(side)
    assert rep["options"] == {
        "scaled": False,
        "row_tolerance": 0.1,
        "tables": ["orders"],
        "source": None,
        "project": None,
    }
    for c in rep["checks"]:
        assert c["category"] in CATEGORIES
        assert c["status"] in ("pass", "fail", "not_measured")
        assert isinstance(c["details"], dict)
    assert {c["table"] for c in rep["checks"]} == {"orders"}
    assert rep["parity"] is True
    assert "parity:" in text


def test_text_lists_failures_first(env, capsys):
    rc, text, _ = run(
        capsys,
        str(env("prod")),
        str(env("dev", drop=("orders", "note"), amount_as_text=True)),
        "--dataset",
    )
    assert rc == 1
    lines = text.splitlines()
    assert lines[2:4] == ["", lines[3]] and lines[3].startswith("FAIL (")
    assert lines.index(lines[3]) < next(i for i, ln in enumerate(lines) if ln.startswith("NOT"))
    assert "orders.note" in text and "orders.amount" in text
    assert text.rstrip().splitlines()[-1].startswith("NO parity: ")


def test_report_helpers_match_the_cli(env):
    a, b = load_side(env("prod"), dataset=True), load_side(env("dev"), dataset=True)
    checks = compare(a, b, policy=resolve_policy())
    rep = build_report(a, b, checks)
    assert rep["parity"] is True
    assert "parity: " in render_text(rep)


# ---- unusable input: exit 2 -------------------------------------------------------------------


def test_missing_input_is_exit_2(env, tmp_path, capsys):
    rc, _, err = run(capsys, str(tmp_path / "nope"), str(env("dev")), "--dataset")
    assert rc == 2 and "no such file" in err


def test_json_that_is_not_a_profile_is_exit_2(env, tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text('{"hello": 1}')
    rc, _, err = run(capsys, str(bad), str(env("dev")), "--dataset")
    assert rc == 2 and "not a Shape profile" in err


def test_folder_of_unrelated_tables_needs_dataset(env, capsys):
    rc, _, err = run(capsys, str(env("prod")), str(env("dev")))
    assert rc == 2 and "--dataset" in err


def test_dataset_flag_on_a_file_is_exit_2(env, capsys):
    f = str(env("prod") / "orders.parquet")
    rc, _, err = run(capsys, f, f, "--dataset")
    assert rc == 2 and "folder" in err


def test_unknown_table_and_bad_flags_are_exit_2(env, capsys):
    a, b = str(env("prod")), str(env("dev"))
    rc, _, err = run(capsys, a, b, "--dataset", "--tables", "nope")
    assert rc == 2 and "nope" in err
    rc, _, err = run(capsys, a, b, "--dataset", "--row-tolerance", "-1")
    assert rc == 2
    rc, _, err = run(capsys, a, b, "--dataset", "--source", "x", "--no-project")
    assert rc == 2


def test_unknown_source_is_exit_2(env, tmp_path, capsys, monkeypatch):
    (tmp_path / "shape.yml").write_text(
        "format: shape-project\nversion: 1\nname: p\nsources:\n  prod:\n    path: data/prod\n",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    rc, _, err = run(capsys, str(env("prod")), str(env("dev")), "--dataset", "--source", "nope")
    assert rc == 2 and "nope" in err


def test_empty_file_is_exit_2(tmp_path, env, capsys):
    empty = tmp_path / "empty.csv"
    empty.write_text("")
    rc, _, err = run(capsys, str(empty), str(env("dev") / "orders.parquet"))
    assert rc == 2


def test_safe_profile_without_tables_is_exit_2(tmp_path, env, capsys):
    bad = tmp_path / "s.json"
    bad.write_text(json.dumps({"redaction_manifest": {}, "tables": {}}))
    with pytest.raises(ParityInputError):
        load_side(bad)
    rc, _, err = run(capsys, str(bad), str(env("dev")), "--dataset")
    assert rc == 2
