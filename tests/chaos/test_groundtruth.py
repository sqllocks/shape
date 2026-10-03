"""Issue #13: named corruptions with a rate and a seed, and the ground-truth log.

The log is checked against the data itself: the cells that differ between the input and the
output are exactly the cells the log lists, with the values it gives."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pyarrow as pa
import pytest

from shape.chaos.groundtruth import (
    CORRUPTIONS,
    Corruption,
    corrupt_tables,
    parse_corruptions,
    read_ground_truth,
    write_ground_truth,
)

N = 400


def customers() -> pa.Table:
    return pa.table(
        {
            "customer_id": pa.array(range(1, N + 1), pa.int64()),
            "segment": pa.array([("gold", "silver", "bronze")[i % 3] for i in range(N)]),
            "notes": pa.array([f"called on day {i}" if i % 7 else None for i in range(N)]),
        }
    )


def orders() -> pa.Table:
    return pa.table(
        {
            "order_id": pa.array(range(1, N + 1), pa.int64()),
            "customer_id": pa.array([(i % N) + 1 for i in range(N)], pa.int64()),
            "amount": pa.array([float(i + 1) if i % 9 else None for i in range(N)]),
            "status": pa.array([("open", "shipped", "closed")[i % 3] for i in range(N)]),
            "placed": pa.array(
                [dt.datetime(2026, 8, 1) + dt.timedelta(hours=i) for i in range(N)],
                pa.timestamp("us"),
            ),
            "ship_day": pa.array([dt.date(2026, 8, 1)] * N, pa.date32()),
            "qty": pa.array([i % 5 + 1 for i in range(N)], pa.int32()),
        }
    )


def tables() -> dict[str, pa.Table]:
    return {"customer": customers(), "order": orders()}


def changed_cells(before: pa.Table, after: pa.Table) -> dict[tuple[int, str], tuple[Any, Any]]:
    cells = {}
    for name in before.column_names:
        b, a = before[name].to_pylist(), after[name].to_pylist()
        for i, (x, y) in enumerate(zip(b, a, strict=False)):
            if x != y:
                cells[(i, name)] = (x, y)
    return cells


def log_cells(outcome: Any, table: str) -> dict[tuple[int, str], dict[str, Any]]:
    return {
        (r["row"], r["column"]): r
        for r in outcome.records
        if r["table"] == table and r["scope"] == "row" and r["column"] is not None
    }


def run(*specs: str, seed: int = 7, batch: int = 0, **kw: Any):
    return corrupt_tables(tables(), parse_corruptions(list(specs)), seed=seed, batch=batch, **kw)


# ---- every corruption: the log is the truth ----------------------------------------------------


@pytest.mark.parametrize(
    "spec, table, column",
    [
        ("orphan_keys=0.05@order.customer_id", "order", "customer_id"),
        ("date_shift=0.1@order.placed:days=5", "order", "placed"),
        ("date_shift=0.1@order.ship_day:days=3", "order", "ship_day"),
        ("negative_amounts=0.1@order.amount", "order", "amount"),
        ("case_whitespace=0.2@order.status", "order", "status"),
        ("pii_fill=0.05@customer.notes", "customer", "notes"),
        ("null_creep=0.1@order.status", "order", "status"),
    ],
)
def test_the_log_lists_exactly_the_changed_cells(spec: str, table: str, column: str) -> None:
    before = tables()[table]
    outcome = run(spec)
    after = outcome.tables[table]
    diff = changed_cells(before, after)
    log = log_cells(outcome, table)
    assert set(diff) == set(log)
    assert len(diff) == round(N * float(spec.split("=")[1].split("@")[0]))  # exact rate
    for (row, col), (was, now) in diff.items():
        rec = log[(row, col)]
        assert col == column
        assert rec["key"] == before[before.column_names[0]][row].as_py()
        assert rec["seed"] == 7 and rec["batch"] == 0
        # `before` and `after` are the cell values (dates and times as ISO text)
        assert rec["before"] == (was.isoformat() if hasattr(was, "isoformat") else was)
        assert rec["after"] == (now.isoformat() if hasattr(now, "isoformat") else now)
    other = set(before.column_names) - {column}
    for name in other:
        assert before[name].equals(after[name])  # nothing else is touched


def test_orphan_keys_match_no_parent() -> None:
    outcome = run(
        "orphan_keys=0.05@order.customer_id",
        references={"order.customer_id": "customer.customer_id"},
    )
    parents = set(outcome.tables["customer"]["customer_id"].to_pylist())
    log = log_cells(outcome, "order")
    assert log
    for (row, _), rec in log.items():
        assert rec["after"] not in parents
        assert outcome.tables["order"]["customer_id"][row].as_py() == rec["after"]
    clean = set(range(N)) - {r for r, _ in log}
    assert all(outcome.tables["order"]["customer_id"][r].as_py() in parents for r in clean)


def test_orphan_keys_on_a_text_key() -> None:
    t = pa.table({"id": ["a", "b", "c", "d"] * 25, "parent_id": ["p1", "p2", "p3", "p4"] * 25})
    out = corrupt_tables(
        {"t": t}, [Corruption("orphan_keys", 0.1, "t", "parent_id")], seed=1, keys={"t": "id"}
    )
    assert out.records and all(r["after"].startswith("ORPHAN-") for r in out.records)


def test_duplicates_append_copies_and_name_their_source() -> None:
    outcome = run("duplicates=0.05@order")
    t, before = outcome.tables["order"], orders()
    assert t.num_rows == N + 20 and outcome.tables["customer"].num_rows == N
    assert t.slice(0, N).equals(before)  # nothing moves
    recs = [r for r in outcome.records if r["kind"] == "duplicates"]
    assert [r["row"] for r in recs] == list(range(N, N + 20))
    for r in recs:
        assert t.slice(r["row"], 1).to_pylist() == before.slice(r["source_row"], 1).to_pylist()
        assert r["key"] == before["order_id"][r["source_row"]].as_py()


def test_date_shift_moves_by_at_most_n_days_and_by_the_logged_amount() -> None:
    outcome = run("date_shift=0.2@order.placed:days=5,direction=late")
    for (row, _), rec in log_cells(outcome, "order").items():
        was = orders()["placed"][row].as_py()
        now = outcome.tables["order"]["placed"][row].as_py()
        assert now - was == dt.timedelta(days=rec["days"]) and 1 <= rec["days"] <= 5


def test_date_shift_defaults_to_every_date_column() -> None:
    columns = {r["column"] for r in run("date_shift=0.1@order").records}
    assert columns == {"placed", "ship_day"}


def test_negative_amounts_flip_positive_values_only() -> None:
    outcome = run("negative_amounts=0.1@order.amount")
    for rec in log_cells(outcome, "order").values():
        assert rec["before"] > 0 and rec["after"] == -rec["before"]


def test_negative_amounts_default_skips_keys() -> None:
    columns = {r["column"] for r in run("negative_amounts=0.1@order").records}
    assert columns == {"amount", "qty"}  # not order_id, not customer_id


def test_case_whitespace_makes_inconsistent_categories() -> None:
    outcome = run("case_whitespace=0.3@order.status")
    forms = {r["after"] for r in outcome.records}
    assert forms - {"open", "shipped", "closed"}
    for r in outcome.records:
        assert r["after"].strip().lower() == r["before"]


def test_pii_fill_writes_ssn_shaped_values() -> None:
    import re

    outcome = run("pii_fill=0.1@customer.notes")
    assert outcome.records
    for r in outcome.records:
        assert re.fullmatch(r"9\d\d-\d\d-\d{4}", r["after"])  # 9xx areas are never issued
    emails = run("pii_fill=0.1@customer.notes:pii=email").records
    assert all(r["after"].endswith("@example.com") for r in emails)


def test_type_change_delivers_the_column_as_text_and_logs_the_column() -> None:
    outcome = run("type_change=1@order.qty")
    assert outcome.tables["order"].schema.field("qty").type == pa.string()
    (rec,) = outcome.records
    assert rec["scope"] == "column" and rec["row"] is None
    assert (rec["before"], rec["after"], rec["rows"]) == ("int32", "string", N)


def test_null_creep_ramps_with_the_batch() -> None:
    counts = []
    for batch in range(4):
        out = run("null_creep=0.02@order.status:step=0.05", batch=batch)
        counts.append(len([r for r in out.records]))
        assert out.tables["order"]["status"].null_count == counts[-1]
    assert counts == [8, 28, 48, 68]  # 2%, 7%, 12%, 17% of 400


# ---- scheduling, determinism ------------------------------------------------------------------


def test_a_corruption_is_active_only_in_its_batches() -> None:
    spec = "negative_amounts=0.1@order.amount:from=2,to=3"
    assert [bool(run(spec, batch=b).records) for b in range(5)] == [False, False, True, True, False]


def test_same_seed_same_tables_and_same_log_and_other_seed_differs(tmp_path) -> None:
    specs = [
        "duplicates=0.02@order",
        "date_shift=0.05@order.placed",
        "pii_fill=0.05@customer.notes",
    ]
    a, b = run(*specs, seed=3), run(*specs, seed=3)
    assert a.records == b.records
    assert all(a.tables[n].equals(b.tables[n]) for n in a.tables)
    c = run(*specs, seed=4)
    assert c.records != a.records
    first = write_ground_truth(tmp_path / "a.jsonl", a).read_bytes()
    assert write_ground_truth(tmp_path / "b.jsonl", b).read_bytes() == first


def test_adding_a_corruption_does_not_change_the_others() -> None:
    one = run("date_shift=0.05@order.placed")
    for specs in (
        ("pii_fill=0.05@customer.notes", "date_shift=0.05@order.placed"),
        ("date_shift=0.05@order.placed", "pii_fill=0.05@customer.notes"),
    ):
        both = run(*specs)
        assert [r for r in both.records if r["kind"] == "date_shift"] == [
            r for r in one.records if r["kind"] == "date_shift"
        ]


def test_the_same_corruption_twice_draws_different_rows() -> None:
    twice = run("negative_amounts=0.05@order.amount", "negative_amounts=0.05@order.amount")
    rows = [r["row"] for r in twice.records]
    assert len(rows) == len(set(rows)) == 2 * round(N * 0.05)  # the second pass finds new rows


def test_inputs_are_never_modified() -> None:
    given = tables()
    corrupt_tables(given, parse_corruptions(["duplicates=0.1", "type_change=1@order.qty"]), seed=1)
    assert given["order"].equals(orders()) and given["customer"].equals(customers())


# ---- the log file ----------------------------------------------------------------------------


def test_the_log_file_is_machine_readable(tmp_path) -> None:
    outcome = run("duplicates=0.02@order", "type_change=1@order.qty")
    path = write_ground_truth(tmp_path / "gt.jsonl", outcome)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    assert lines[0]["record"] == "run" and lines[0]["seed"] == 7
    assert lines[0]["tables"]["order"] == {"rows_in": N, "rows_out": N + 8}
    assert lines[0]["changes"] == len(lines) - 1
    assert {x["record"] for x in lines[1:]} == {"change"}
    header, changes = read_ground_truth(path)
    assert header["corruptions"][0]["kind"] == "duplicates" and len(changes) == len(lines) - 1


def test_a_file_that_is_not_a_log_is_refused(tmp_path) -> None:
    (tmp_path / "x.jsonl").write_text('{"a": 1}\n')
    with pytest.raises(ValueError, match="ground-truth"):
        read_ground_truth(tmp_path / "x.jsonl")


# ---- parsing and errors -----------------------------------------------------------------------


def test_parse() -> None:
    c = Corruption.parse("date_shift=0.03@order.placed:days=14,from=2,to=9,direction=late")
    assert (c.kind, c.rate, c.table, c.column) == ("date_shift", 0.03, "order", "placed")
    assert (c.start_batch, c.end_batch, c.options) == (2, 9, {"days": 14, "direction": "late"})
    assert Corruption.parse("duplicates").rate == 0.02


@pytest.mark.parametrize(
    "spec",
    [
        "shred=0.1",
        "duplicates=2",
        "duplicates=x",
        "pii_fill=0.1",  # needs a column
        "type_change=1@order",
        "duplicates=0.1:color=red",
        "date_shift=0.1@order.placed:days=0",
        "pii_fill=0.1@customer.notes:pii=iban",
        "null_creep=0.1@order.status:step=-1",
        "duplicates=0.1:from=3,to=1",
    ],
)
def test_bad_specs_are_errors(spec: str) -> None:
    with pytest.raises(ValueError):
        Corruption.parse(spec)


@pytest.mark.parametrize(
    "spec",
    [
        "duplicates=0.1@nope",
        "pii_fill=0.1@nope.notes",
        "pii_fill=0.1@customer.nope",
        "pii_fill=0.1@customer.customer_id",  # not text
        "type_change=1@customer.segment",  # already text
        "date_shift=0.1@order.status",  # not a date
        "negative_amounts=0.1@order.status",
        "case_whitespace=0.1@order.qty",
    ],
)
def test_impossible_corruptions_are_errors_not_silent(spec: str) -> None:
    with pytest.raises(ValueError):
        run(spec)


def test_the_kinds_are_the_ones_the_issue_names() -> None:
    assert {"duplicates", "orphan_keys", "date_shift", "pii_fill", "type_change"} <= set(
        CORRUPTIONS
    )
    with pytest.raises(ValueError, match="at least one"):
        parse_corruptions([])
