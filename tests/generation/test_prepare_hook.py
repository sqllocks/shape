"""P6-01-perf: a strategy may name a whole-table result that needs no generated data
(``prepare(spec, ctx)``), and the engine builds it on a helper thread at the start of generation.
The result is the same however it is reached, it is built once, and a column the hook does not fit
is left to its own chunks."""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path
from typing import Any

import pytest

from shape.builtins.strategies import keys
from shape.builtins.strategies.keys import ForeignKey
from shape.generation.engine import Engine, EngineContext

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "benchmarks" / "vs_spindle"
sys.path.insert(0, str(BENCH / "strategy_1to1"))
sys.path.insert(0, str(BENCH))

import relational_cases as rc  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "vs_spindle_schema_import_prepare", BENCH / "schema_import.py"
)
assert _spec and _spec.loader
schema_import = importlib.util.module_from_spec(_spec)
sys.modules["vs_spindle_schema_import_prepare"] = schema_import
_spec.loader.exec_module(schema_import)

CASE = "foreign_key/pareto_capped"


def _engine(case_id: str = CASE, **kw: Any) -> Engine:
    return Engine(schema_import.import_dump(rc.schema_for(rc.CASES[case_id])), seed=1042, **kw)


def _context(engine: Engine, table: str, column: str) -> EngineContext:
    return EngineContext(
        seed=engine.seed,
        table=table,
        column=column,
        chunk=0,
        row_start=0,
        n_rows=0,
        columns={},
        engine=engine,
        column_def=engine.schema.tables[table].columns[column],
    )


def _capped_columns(engine: Engine) -> list[tuple[str, str]]:
    return [
        (t, c.name)
        for t, tdef in engine.schema.tables.items()
        for c in tdef.columns.values()
        if c.strategy == "foreign_key" and c.generator.get("distribution") == "pareto"
    ]


def test_prepare_returns_the_cap_build_for_a_plain_capped_key():
    engine = _engine()
    (table, column), *_ = _capped_columns(engine)
    spec = engine.schema.tables[table].columns[column].generator
    job = ForeignKey().prepare(spec, _context(engine, table, column))
    assert job is not None
    assert not [k for k in engine._memo if k[0] == "fk-cap"]
    job()
    assert len([k for k in engine._memo if k[0] == "fk-cap"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"distribution": "zipf"},
        {"distribution": "uniform"},
        {"constrained_by": "other"},
        {"sample_rate": 0.5},
        {"fan_out": {"top_fraction": 0.2, "top_share": 0.8}},
        {"params": {"alpha": 1.2}, "max_per_parent": None},  # no cap anywhere
        {"ref": "no_dot"},
    ],
)
def test_prepare_leaves_every_other_key_to_its_own_chunks(change):
    engine = _engine()
    (table, column), *_ = _capped_columns(engine)
    spec = {**engine.schema.tables[table].columns[column].generator, **change}
    if spec.get("max_per_parent", 1) is None:
        del spec["max_per_parent"]
    assert ForeignKey().prepare(spec, _context(engine, table, column)) is None


def test_prepare_leaves_a_key_to_a_parent_whose_keys_are_not_a_plain_sequence():
    engine = _engine()
    (table, column), *_ = _capped_columns(engine)
    spec = engine.schema.tables[table].columns[column].generator
    parent = spec["ref"].split(".")[0]
    engine.schema.tables[parent].columns[spec["ref"].split(".")[1]].generator["strategy"] = "uuid"
    assert ForeignKey().prepare(spec, _context(engine, table, column)) is None


def test_the_cap_is_built_once_over_a_whole_run_and_the_tables_do_not_change(monkeypatch):
    calls: list[str] = []
    real = keys.cap_per_parent

    def counting(*args: Any, **kwargs: Any):
        calls.append(threading.current_thread().name)
        return real(*args, **kwargs)

    plain = _engine().generate().tables
    monkeypatch.setattr(keys, "cap_per_parent", counting)
    for threads in ("1", "3"):
        monkeypatch.setenv("SHAPE_THREADS", threads)
        calls.clear()
        tables = _engine(chunk_rows=50).generate().tables
        assert len(calls) == len(_capped_columns(_engine())), (threads, calls)
        for name, table in plain.items():
            assert table.equals(tables[name]), (name, threads)


def test_a_prepare_that_raises_is_left_to_the_column(monkeypatch):
    def broken(self: ForeignKey, spec: Any, ctx: Any):
        raise RuntimeError("prepare failed")

    plain = _engine().generate().tables
    monkeypatch.setattr(ForeignKey, "prepare", broken)
    tables = _engine().generate().tables
    for name, table in plain.items():
        assert table.equals(tables[name]), name


def test_a_job_that_raises_does_not_stop_the_run(monkeypatch):
    def failing(self: ForeignKey, spec: Any, ctx: Any):
        def job() -> None:
            raise RuntimeError("build failed")

        return job

    plain = _engine().generate().tables
    monkeypatch.setattr(ForeignKey, "prepare", failing)
    tables = _engine().generate().tables
    for name, table in plain.items():
        assert table.equals(tables[name]), name
