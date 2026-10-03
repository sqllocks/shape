"""W3-10: the contract's optional ``timeseries`` and ``reconcile`` rules (additive keys of
contract v1), evaluated by ``shape.check(profile, contract, data=...)`` and
``shape check --data``."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import shape
from shape.cli.main import main
from shape.contracts.v1 import ContractError

T0 = datetime(2026, 1, 1)


def readings(skip=()):
    times = [T0 + timedelta(hours=i) for i in range(8) if i not in skip]
    return pa.table({"ts": pa.array(times, pa.timestamp("us")), "v": list(range(len(times)))})


GAPS = {"table": "readings", "time": "ts", "every": "1h", "gaps": {"max_missing": 0}}


def profile(table=None):
    return shape.profile({"readings": table or readings()})


def contract(**extra):
    return {"tables": {"readings": {}}, **extra}


def test_timeseries_rules_pass_and_fail_through_check():
    p = profile()
    c = contract(timeseries=[GAPS])
    assert shape.check(p, c, data={"readings": readings()}).passed
    res = shape.check(p, c, data={"readings": readings(skip=(3,))})
    assert not res.passed
    (v,) = res.violations
    assert v["rule"] == "timeseries.gaps" and v["column"] == "readings.ts"
    assert v["expected"] == {"max_missing": 0} and v["observed"]["missing"] == 1


def test_warnings_are_not_violations():
    t = pa.table({"ts": pa.array([T0, T0, T0 + timedelta(hours=1)], pa.timestamp("us"))})
    res = shape.check(
        shape.profile({"readings": t}), contract(timeseries=[GAPS]), data={"readings": t}
    )
    assert res.passed


def test_reconcile_rules_use_the_loaded_tables():
    src, dst = readings(), readings(skip=(3,))
    rule = {
        "name": "readings vs mirror",
        "source": {"table": "readings"},
        "target": {"table": "mirror"},
        "aggregates": [{"column": "v", "agg": "sum"}],
    }
    p = shape.profile({"readings": src, "mirror": dst})
    c = {"tables": {"readings": {}, "mirror": {}}, "reconcile": [rule]}
    res = shape.check(p, c, data={"readings": src, "mirror": dst})
    assert not res.passed
    assert {v["rule"] for v in res.violations} == {"reconcile.count", "reconcile.aggregate"}
    assert all(v["reconciliation"] == "readings vs mirror" for v in res.violations)
    assert {v["column"] for v in res.violations} == {None, "v"}
    assert shape.check(p, c, data={"readings": src, "mirror": src}).passed


def test_a_single_table_contract_takes_the_rules_too():
    t = readings(skip=(3,))
    res = shape.check(
        shape.profile(t, name="readings"), {"timeseries": [GAPS]}, data={"readings": t}
    )
    assert [v["rule"] for v in res.violations] == ["timeseries.gaps"]


def test_rules_that_need_data_refuse_to_pass_without_it():
    with pytest.raises(ContractError, match="timeseries.*data"):
        shape.check(profile(), contract(timeseries=[GAPS]))
    rule = {"source": "a.csv", "target": "b.csv"}
    with pytest.raises(ContractError, match="reconcile.*data"):
        shape.check(profile(), contract(reconcile=[rule]))


def test_data_can_be_a_path(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    pq.write_table(readings(skip=(3,)), d / "readings.parquet")
    res = shape.check(profile(), contract(timeseries=[GAPS]), data=d)
    assert [v["rule"] for v in res.violations] == ["timeseries.gaps"]


def test_bad_rules_are_contract_errors():
    with pytest.raises(ContractError, match=r"timeseries\[0\]"):
        shape.check(profile(), contract(timeseries=[{"table": "readings"}]), data={})
    with pytest.raises(ContractError, match=r"reconcile\[0\]"):
        shape.check(profile(), contract(reconcile=[{"source": "a"}]), data={})


def test_the_rules_belong_at_the_top_level_not_inside_a_table():
    c = {"tables": {"readings": {"timeseries": [GAPS]}}}
    with pytest.raises(ContractError, match="top level"):
        shape.check(profile(), c, data={})


def test_an_unknown_rule_is_still_refused():
    # the compatibility rule of contract v1: a reader that does not know a rule refuses it
    with pytest.raises(ContractError, match="unknown contract keys"):
        shape.check(profile(), contract(timeseries_quality=[GAPS]))


def test_contracts_without_the_new_rules_are_unchanged():
    assert shape.check(profile(), contract()).passed
    assert shape.check(profile(), contract(), data={"readings": readings()}).passed


def test_the_cli_checks_a_contract_with_data(tmp_path, capsys):
    prof = tmp_path / "p.shape"
    shape.save(shape.profile({"readings": readings()}), str(prof))
    d = tmp_path / "d"
    d.mkdir()
    pq.write_table(readings(skip=(3,)), d / "readings.parquet")
    c = tmp_path / "c.json"
    c.write_text(json.dumps(contract(timeseries=[GAPS])))
    assert main(["check", str(prof), str(c), "--data", str(d)]) == 1
    assert "timeseries.gaps" in capsys.readouterr().out
    # without --data the contract cannot be evaluated: exit 2, not a silent pass
    assert main(["check", str(prof), str(c)]) == 2
    assert "data" in capsys.readouterr().err
    c.write_text(json.dumps(contract(timeseries=[{**GAPS, "gaps": {"max_missing": 1}}])))
    assert main(["check", str(prof), str(c), "--data", str(d)]) == 0
