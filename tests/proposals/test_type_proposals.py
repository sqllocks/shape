"""W2-07 item 7: ``type`` proposals, and how accepted ones are applied."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest

import shape
from shape.cli.main import main
from shape.proposals import (
    DEFAULT_KINDS,
    KINDS,
    DecisionError,
    DecisionFile,
    apply_decisions,
    propose,
    propose_types,
)

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _w2_07_data import orders_table  # noqa: E402

NOW = "2026-10-03T12:00:00Z"
FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "w2_07"


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    try:
        code = main(list(argv))
    except SystemExit as exc:
        code = int(exc.code or 0)
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.fixture()
def bad(tmp_path: Path) -> tuple[Any, Path]:
    n = 60
    table = pa.table(
        {
            "qty": pa.array([str(i) for i in range(n)]),
            "price": pa.array([float(i) for i in range(n)]),
            "day": pa.array([f"2026-01-{1 + i % 28:02d}" for i in range(n)]),
            "zip": pa.array([10000 + i % 20 for i in range(n)]),
            "name": pa.array([f"person {i}" for i in range(n)]),
        }
    )
    path = tmp_path / "bad.parquet"
    pq.write_table(table, path)
    prof = shape.profile(str(path))
    saved = tmp_path / "bad.shape"
    shape.save(prof, saved)
    return prof, saved


# --- proposing ---------------------------------------------------------------------------------


def test_the_type_kind_exists_and_is_not_proposed_by_default(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    assert "type" in KINDS and "type" not in DEFAULT_KINDS
    assert set(DEFAULT_KINDS) == {"relationship", "pii", "semantic"}
    assert not [p for p in propose(prof) if p.kind == "type"]


def test_findings_become_type_proposals_with_evidence(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    got = {p.id: p for p in propose(prof, kinds=["type"], min_confidence=0.0)}
    assert set(got) == {"type:bad.qty", "type:bad.price", "type:bad.day", "type:bad.zip"}
    qty = got["type:bad.qty"]
    assert (
        qty.kind == "type"
        and qty.subject == "bad.qty"
        and qty.claim["type"] == "integer"
        and qty.claim["table"] == "bad"
        and qty.claim["column"] == "qty"
    )
    assert qty.evidence["finding"] == "declared_differs"
    assert qty.evidence["declared"] == "string" and qty.evidence["inferred"] == "integer"
    assert qty.evidence["confidence"] == 1.0 and qty.evidence["parse_shares"]["integer"] == 1.0
    assert got["type:bad.price"].claim["type"] == "integer"
    assert got["type:bad.day"].claim["type"] == "date"
    zipc = got["type:bad.zip"]
    assert zipc.claim["type"] == "string" and zipc.evidence["finding"] == "identifier_suspect"
    assert zipc.evidence["identifier"] and zipc.evidence["parse_shares"]["integer"] == 1.0
    for p in got.values():
        assert 0.0 <= p.confidence <= 1.0


def test_every_identifier_suspect_is_proposed_even_in_a_csv(tmp_path: Path) -> None:
    p = tmp_path / "s.csv"
    p.write_text("reading,n\n" + "".join(f"{10000 + i},{i}\n" for i in range(60)))
    with pytest.warns(UserWarning):
        prof = shape.profile(str(p))
    (prop,) = propose_types(prof)
    assert prop.id == "type:s.reading" and prop.claim["type"] == "string"


def test_a_low_confidence_inferred_type_is_proposed_as_its_candidate(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("qty\n" + "\n".join([str(i % 8) for i in range(97)] + ["x", "y", "z"]) + "\n")
    (prop,) = propose_types(shape.profile(str(p)))
    assert prop.claim["type"] == "integer" and prop.evidence["confidence"] == 0.97
    assert prop.evidence["finding"] == "low_confidence"


def test_the_proposal_confidence_filters(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    all_ = propose(prof, kinds=["type"], min_confidence=0.0)
    high = propose(prof, kinds=["type"], min_confidence=0.85)
    assert len(high) < len(all_)
    assert all(p.confidence >= 0.85 for p in high)
    zipc = next(p for p in all_ if p.id == "type:bad.zip")
    assert zipc.confidence < 0.85


def test_a_clean_profile_and_an_old_profile_propose_nothing() -> None:
    assert propose_types(shape.profile(orders_table(300))) == []
    old = shape.load(str(FIXTURES / "pre_w2_07_orders.shape"))
    assert propose_types(old) == []
    assert propose(old, kinds=["type"]) == []


def test_unknown_kinds_still_fail() -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        propose(shape.profile(orders_table(60)), kinds=["types"])


# --- the decision file -------------------------------------------------------------------------


def test_a_decision_file_keeps_type_proposals_and_stays_version_1(
    bad: tuple[Any, Path], tmp_path: Path
) -> None:
    prof, _ = bad
    df = DecisionFile.empty()
    df.update(propose(prof, kinds=["type"]), kinds=["type"], now=NOW)
    path = tmp_path / "d.json"
    df.write(path)
    doc = json.loads(path.read_text())
    assert doc["format"] == "shape-decisions" and doc["version"] == 1
    assert {p["kind"] for p in doc["proposals"]} == {"type"}
    assert DecisionFile.read(path).dumps() == path.read_text()  # round trip, no change


def test_the_json_schema_accepts_the_type_kind(bad: tuple[Any, Path], tmp_path: Path) -> None:
    schema = json.loads(
        (Path(shape.__file__).parent / "schemas" / "decisions-v1.schema.json").read_text()
    )
    assert "type" in schema["$defs"]["proposal"]["properties"]["kind"]["enum"]


def test_a_default_update_leaves_pending_type_proposals_alone(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    df = DecisionFile.empty()
    df.update(propose(prof, kinds=["type"]), kinds=["type"], now=NOW)
    before = {e.proposal.id for e in df.entries()}
    df.update(propose(prof), now=NOW)  # a default run proposes the other kinds
    assert before <= {e.proposal.id for e in df.entries()}


def test_a_type_run_withdraws_a_pending_proposal_the_data_no_longer_has(
    bad: tuple[Any, Path],
) -> None:
    prof, _ = bad
    df = DecisionFile.empty()
    df.update(propose(prof, kinds=["type"]), kinds=["type"], now=NOW)
    assert "type:bad.qty" in {e.proposal.id for e in df.entries()}
    clean = shape.profile(pa.table({"qty": pa.array(range(60))}), name="bad")
    result = df.update(propose(clean, kinds=["type"]), kinds=["type"], now=NOW)
    assert "type:bad.qty" in result.withdrawn


def test_a_rejected_type_proposal_is_not_proposed_again(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    df = DecisionFile.empty()
    df.update(propose(prof, kinds=["type"]), kinds=["type"], now=NOW)
    df.decide("type:bad.zip", "rejected", actor="ana", now=NOW)
    result = df.update(propose(prof, kinds=["type"]), kinds=["type"], now=NOW)
    assert "type:bad.zip" in result.skipped_rejected


def test_auto_accept_is_off_unless_given(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    df = DecisionFile.empty()
    result = df.update(propose(prof, kinds=["type"]), kinds=["type"], now=NOW)
    assert result.auto_accepted == () and df.accepted() == []
    df.update(propose(prof, kinds=["type"]), kinds=["type"], auto_accept=0.85, now=NOW)
    assert {e.proposal.id for e in df.accepted()} >= {"type:bad.qty"}


# --- the command line --------------------------------------------------------------------------


def test_propose_list_and_decide_through_the_cli(
    bad: tuple[Any, Path], tmp_path: Path, capsys: Any
) -> None:
    _, saved = bad
    dec = tmp_path / "d.json"
    code, out, _ = run(
        capsys, "proposals", "propose", str(saved), "--kinds", "type", "-d", str(dec)
    )
    assert code == 0 and json.loads(out)["added"] == 4
    code, out, _ = run(capsys, "proposals", "list", "-d", str(dec), "--kind", "type", "--json")
    rows = json.loads(out)
    assert {r["id"] for r in rows} == {
        "type:bad.qty",
        "type:bad.price",
        "type:bad.day",
        "type:bad.zip",
    }
    assert {r["status"] for r in rows} == {"pending"}  # nothing is accepted automatically
    code, _, _ = run(
        capsys, "proposals", "decide", "-d", str(dec), "type:bad.zip", "accept", "--actor", "ana"
    )
    assert code == 0
    code, out, _ = run(capsys, "proposals", "propose", str(saved), "-d", str(dec))  # default kinds
    assert code == 0
    ids = {e.proposal.id for e in DecisionFile.read(dec).entries()}
    assert "type:bad.zip" in ids  # a default run does not withdraw the type proposals


def test_the_default_cli_run_proposes_no_types(
    bad: tuple[Any, Path], tmp_path: Path, capsys: Any
) -> None:
    _, saved = bad
    dec = tmp_path / "d.json"
    assert run(capsys, "proposals", "propose", str(saved), "-d", str(dec))[0] == 0
    assert not [e for e in DecisionFile.read(dec).entries() if e.proposal.kind == "type"]


def test_an_unknown_kind_exits_2(bad: tuple[Any, Path], tmp_path: Path, capsys: Any) -> None:
    _, saved = bad
    code, _, err = run(
        capsys,
        "proposals",
        "propose",
        str(saved),
        "--kinds",
        "typo",
        "-d",
        str(tmp_path / "d.json"),
    )
    assert code == 2 and err.startswith("shape: error:")


def test_the_cli_kind_list_includes_type() -> None:
    from shape.cli.proposals import _KINDS

    assert _KINDS == KINDS and "type" in _KINDS


# --- applying accepted decisions ---------------------------------------------------------------


def _accept(prof: Any, *ids: str, kinds: tuple[str, ...] = ("type",)) -> DecisionFile:
    df = DecisionFile.empty()
    df.update(propose(prof, kinds=list(kinds), min_confidence=0.0), kinds=list(kinds), now=NOW)
    for i in ids:
        df.decide(i, "accepted", actor="ana", now=NOW)
    return df


def test_accepting_a_suspect_makes_the_column_text_in_the_profile(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    out = apply_decisions(prof, _accept(prof, "type:bad.zip"))
    z = out.tables["bad"]["columns"]["zip"]
    assert z["dtype"] == "string" and z["mean"] is None and z["distribution"] is None
    assert z["string_length"]["min"] == z["string_length"]["max"] == 5.0
    assert prof.tables["bad"]["columns"]["zip"]["dtype"] == "integer"  # the input is not changed
    assert apply_decisions(out, _accept(prof, "type:bad.zip")).to_dict() == out.to_dict()


def test_pending_rejected_and_deferred_type_decisions_change_nothing(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    df = DecisionFile.empty()
    df.update(propose(prof, kinds=["type"], min_confidence=0.0), kinds=["type"], now=NOW)
    df.decide("type:bad.zip", "rejected", actor="a", now=NOW)
    df.decide("type:bad.qty", "deferred", actor="a", now=NOW)
    assert apply_decisions(prof, df).to_dict() == prof.to_dict()


def test_an_accepted_type_the_profile_already_has_is_a_no_op(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    out = apply_decisions(prof, _accept(prof, "type:bad.qty", "type:bad.price"))
    assert out.tables["bad"]["columns"]["qty"]["dtype"] == "integer"
    assert out.to_dict() == prof.to_dict()


def test_a_date_decision_relabels_the_datetime_the_profile_reports(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    assert prof.tables["bad"]["columns"]["day"]["dtype"] == "datetime"
    out = apply_decisions(prof, _accept(prof, "type:bad.day"))
    assert out.tables["bad"]["columns"]["day"]["dtype"] == "date"


def test_a_proposal_for_a_column_the_profile_lacks_is_an_error(bad: tuple[Any, Path]) -> None:
    prof, _ = bad
    other = shape.profile(pa.table({"a": pa.array(range(40))}), name="bad")
    with pytest.raises(DecisionError, match="zip"):
        apply_decisions(other, _accept(prof, "type:bad.zip"))


def test_generation_keeps_an_accepted_text_type_and_its_width(bad: tuple[Any, Path]) -> None:
    from shape.generation.engine import Engine
    from shape.generation.fit import PRESET, fit_schema

    prof, _ = bad
    plain = fit_schema(prof).schema.to_dict()["tables"]["bad"]["columns"]["zip"]["type"]
    fitted = fit_schema(prof, decisions=_accept(prof, "type:bad.zip"))
    col = fitted.schema.to_dict()["tables"]["bad"]["columns"]["zip"]
    assert plain == "integer" and col["type"] == "string"
    table = Engine(fitted.schema, scale=PRESET, seed=3).generate()["bad"]
    values = table["zip"].to_pylist()
    assert all(isinstance(v, str) and len(v) == 5 for v in values)


def test_a_low_confidence_type_is_applied_from_a_complete_frequency_table(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("qty\n" + "\n".join([str(i % 8) for i in range(97)] + ["x", "y", "z"]) + "\n")
    prof = shape.profile(str(p))
    out = apply_decisions(prof, _accept(prof, "type:t.qty"))
    # the 97 numeric values keep their frequencies; the three stray ones are not numbers
    assert out.tables["t"]["columns"]["qty"]["dtype"] == "integer"


def test_a_type_that_cannot_be_applied_from_the_profile_alone_is_an_error(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text(
        "qty\n" + "\n".join([str(i) for i in range(700)] + [f"w{i}" for i in range(30)]) + "\n"
    )
    prof = shape.profile(str(p))
    with pytest.raises(DecisionError, match="re-profile"):
        apply_decisions(prof, _accept(prof, "type:t.qty"))


def test_plan_and_generate_apply_accepted_types(
    bad: tuple[Any, Path], tmp_path: Path, capsys: Any
) -> None:
    _, saved = bad
    dec = tmp_path / "d.json"
    run(capsys, "proposals", "propose", str(saved), "--kinds", "type", "-d", str(dec))
    run(capsys, "proposals", "decide", "-d", str(dec), "type:bad.zip", "accept", "--actor", "ana")
    code, out, _ = run(capsys, "plan", str(saved), "--decisions", str(dec))
    assert code == 0 and "bad.zip" in out
    plan = json.loads(out)
    assert any("bad.zip" in i["evidence"] for i in plan["items"])
    gen = [
        "generate",
        "--from",
        str(saved),
        "--decisions",
        str(dec),
        "-o",
        str(tmp_path / "out"),
        "--format",
        "csv",
    ]
    code, _, err = run(capsys, *gen)
    assert code == 0, err
    import csv

    with open(tmp_path / "out" / "bad.csv", newline="") as fh:
        zips = [row["zip"] for row in csv.DictReader(fh)]
    assert zips and all(len(z) == 5 for z in zips)


def test_profile_reads_accepted_type_decisions_as_types(tmp_path: Path, capsys: Any) -> None:
    csv = tmp_path / "t.csv"
    csv.write_text("reading,qty\n" + "".join(f"{10000 + i},{i}\n" for i in range(60)))
    with pytest.warns(UserWarning):
        first = tmp_path / "first.shape"
        assert run(capsys, "profile", str(csv), "-o", str(first))[0] == 0
    dec = tmp_path / "d.json"
    run(capsys, "proposals", "propose", str(first), "--kinds", "type", "-d", str(dec))
    run(capsys, "proposals", "decide", "-d", str(dec), "type:t.reading", "accept", "--actor", "ana")
    second = tmp_path / "second.shape"
    code, _, err = run(capsys, "profile", str(csv), "--decisions", str(dec), "-o", str(second))
    assert code == 0, err
    col = shape.load(str(second)).tables["t"]["columns"]["reading"]
    assert col["dtype"] == "string" and col["type_inference"]["source"] == "option"
    assert col["min_value"][1] == "10000"  # the text, not a number


def test_profile_decisions_do_not_override_an_explicit_types_file(
    tmp_path: Path, capsys: Any
) -> None:
    csv = tmp_path / "t.csv"
    csv.write_text("reading,qty\n" + "".join(f"{10000 + i},{i}\n" for i in range(60)))
    dec = tmp_path / "d.json"
    df = DecisionFile.empty()
    df.update(
        [
            __import__("shape.proposals", fromlist=["Proposal"]).Proposal(
                "type:t.reading", "type", "t.reading", {"type": "string"}, 0.6, {}
            )
        ],
        kinds=["type"], now=NOW,
    )  # fmt: skip
    df.decide("type:t.reading", "accepted", actor="a", now=NOW)
    df.write(dec)
    types = tmp_path / "types.json"
    types.write_text(json.dumps({"reading": "float"}))
    out = tmp_path / "o.shape"
    code, _, _ = run(
        capsys, "profile", str(csv), "--decisions", str(dec), "--types", str(types), "-o", str(out)
    )
    assert code == 0
    col = shape.load(str(out)).tables["t"]["columns"]["reading"]
    assert col["type_inference"]["source"] == "option" and col["dtype"] != "string"


def test_profile_decisions_for_another_table_are_ignored(tmp_path: Path, capsys: Any) -> None:
    csv = tmp_path / "t.csv"
    csv.write_text("reading,qty\n" + "".join(f"{10000 + i},{i}\n" for i in range(60)))
    dec = tmp_path / "d.json"
    from shape.proposals import Proposal

    df = DecisionFile.empty()
    other = Proposal("type:other.reading", "type", "other.reading", {"type": "string"}, 0.6, {})
    df.update([other], kinds=["type"], now=NOW)
    df.decide("type:other.reading", "accepted", actor="a", now=NOW)
    df.write(dec)
    out = tmp_path / "o.shape"
    with pytest.warns(UserWarning):
        code, _, _ = run(capsys, "profile", str(csv), "--decisions", str(dec), "-o", str(out))
    assert code == 0
    assert shape.load(str(out)).tables["t"]["columns"]["reading"]["dtype"] == "integer"


def test_profile_with_a_bad_decision_file_exits_2(tmp_path: Path, capsys: Any) -> None:
    csv = tmp_path / "t.csv"
    csv.write_text("a\n1\n2\n")
    bad_file = tmp_path / "d.json"
    bad_file.write_text("{}")
    code, _, err = run(
        capsys, "profile", str(csv), "--decisions", str(bad_file), "-o", str(tmp_path / "o.shape")
    )
    assert code == 2 and "format" in err
    code, _, _ = run(
        capsys,
        "profile",
        str(csv),
        "--decisions",
        str(tmp_path / "none.json"),
        "-o",
        str(tmp_path / "o.shape"),
    )
    assert code == 2


def test_profile_decisions_apply_per_table_in_a_dataset(tmp_path: Path, capsys: Any) -> None:
    folder = tmp_path / "ds"
    folder.mkdir()
    (folder / "a.csv").write_text("reading,n\n" + "".join(f"{10000 + i},{i}\n" for i in range(40)))
    (folder / "b.csv").write_text("reading,n\n" + "".join(f"{10000 + i},{i}\n" for i in range(40)))
    from shape.proposals import Proposal

    df = DecisionFile.empty()
    df.update([Proposal("type:a.reading", "type", "a.reading", {"type": "string"}, 0.6, {})],
              kinds=["type"], now=NOW)  # fmt: skip
    df.decide("type:a.reading", "accepted", actor="a", now=NOW)
    dec = tmp_path / "d.json"
    df.write(dec)
    out = tmp_path / "o.shape"
    with pytest.warns(UserWarning):
        code, _, err = run(
            capsys, "profile", str(folder), "--dataset", "--decisions", str(dec), "-o", str(out)
        )
    assert code == 0, err
    prof = shape.load(str(out))
    assert prof.tables["a"]["columns"]["reading"]["dtype"] == "string"
    assert prof.tables["b"]["columns"]["reading"]["dtype"] == "integer"
