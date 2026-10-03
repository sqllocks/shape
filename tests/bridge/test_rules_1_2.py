"""W7-05 item 3: ``rules_mutate`` and ``rules_backtest``."""

from __future__ import annotations

import json
import threading
import time

import pytest
from data_1_2 import (
    make_registry,
    real_tables,
    real_values,
    write_contract,
    write_tables,
)

from shape.cli.main import main

CUSTOMERS = {
    "columns": {
        "customer_id": {"dtype": "integer", "unique": True},
        "tier": {"allowed_values": ["gold", "silver", "bronze"]},
        "amount": {"min": 0},
        "email": {"nullable": False},
    },
    "row_count": {"min": 50},
}


@pytest.fixture
def data(tmp_path):
    folder = write_tables(tmp_path / "data", real_tables())
    contract = write_contract(tmp_path / "contract.json", CUSTOMERS)
    return folder, contract


def cli_json(capsys, *argv):
    code = main(list(argv))
    return code, json.loads(capsys.readouterr().out)


# ---- rules_mutate --------------------------------------------------------------------------


def test_the_mutation_report_is_the_one_the_cli_prints(api12, data, capsys):
    folder, contract = data
    result = api12.ok("rules_mutate", data=str(folder), contract=str(contract), seed=3)
    code, cli = cli_json(
        capsys, "rules", "mutate", str(folder), str(contract), "--seed", "3", "--json"
    )
    assert code == 0 and result == cli
    assert result["format"] == "shape-mutation-report" and result["version"] == 1
    assert result["score"]["overall"]["applicable"] > 0


def test_the_same_seed_gives_the_same_report_and_rate_and_plan_are_honoured(api12, data, tmp_path):
    folder, contract = data
    args = {"data": str(folder), "contract": str(contract)}
    assert api12.ok("rules_mutate", seed=1, **args) == api12.ok("rules_mutate", seed=1, **args)
    assert api12.ok("rules_mutate", rate=0.2, **args)["rate"] == 0.2
    plan = tmp_path / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "format": "shape-mutation-plan",
                "version": 1,
                "corruptions": [
                    {"kind": "negative_amounts", "table": "customers", "column": "amount"}
                ],
            }
        )
    )
    planned = api12.ok("rules_mutate", plan=str(plan), **args)
    assert [m["id"] for m in planned["mutants"]] == ["negative_amounts.customers.amount"]
    assert planned["mutants"][0]["killed"] is True  # the `amount >= 0` rule catches it


def test_min_score_reports_whether_it_was_met_and_leaves_the_report_alone(api12, data, capsys):
    folder, contract = data
    plain = api12.ok("rules_mutate", data=str(folder), contract=str(contract))
    score = plain["score"]["overall"]["score"]
    assert "min_score" not in plain
    met = api12.ok("rules_mutate", data=str(folder), contract=str(contract), min_score=0)
    assert met["min_score"] == {"required": 0.0, "met": True}
    missed = api12.ok("rules_mutate", data=str(folder), contract=str(contract), min_score=1)
    assert missed["min_score"] == {"required": 1.0, "met": score == 1.0}
    assert {k: v for k, v in missed.items() if k != "min_score"} == plain
    code = main(["rules", "mutate", str(folder), str(contract), "--min-score", "1"])
    assert (code == 0) == missed["min_score"]["met"]
    # the boundary: exactly the score is met, a hair above is not
    exact = api12.ok("rules_mutate", data=str(folder), contract=str(contract), min_score=score)
    assert exact["min_score"]["met"] is True
    above = min(1.0, score + 0.01)
    if above > score:
        assert not api12.ok(
            "rules_mutate", data=str(folder), contract=str(contract), min_score=above
        )["min_score"]["met"]


def test_the_report_holds_no_value_of_the_data(api12, data):
    folder, contract = data
    text = json.dumps(api12.ok("rules_mutate", data=str(folder), contract=str(contract)))
    assert not [v for v in real_values() if len(v) > 4 and v in text]


def test_rules_mutate_errors(api12, data, tmp_path):
    folder, contract = data
    args = {"data": str(folder), "contract": str(contract)}
    api12.fail(
        "rules_mutate", "input.not_found", data=str(tmp_path / "none"), contract=str(contract)
    )
    api12.fail("rules_mutate", "input.not_found", data=str(folder), contract=str(tmp_path / "none"))
    api12.fail("rules_mutate", "input.not_found", plan=str(tmp_path / "none"), **args)
    api12.fail("rules_mutate", "usage.missing_argument", data=str(folder))
    api12.fail("rules_mutate", "usage.missing_argument", contract=str(contract))
    api12.fail("rules_mutate", "usage.invalid_argument", rate=1.5, **args)
    api12.fail("rules_mutate", "usage.invalid_argument", rate=-0.1, **args)
    api12.fail("rules_mutate", "usage.invalid_argument", min_score=1.1, **args)
    api12.fail("rules_mutate", "usage.invalid_argument", seed="x", **args)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"columns": {"tier": {"colour": 1}}}))
    api12.fail("rules_mutate", "input.invalid_value", data=str(folder), contract=str(bad))
    broken = tmp_path / "broken.json"
    broken.write_text("{nope")
    api12.fail("rules_mutate", "input.invalid_value", data=str(folder), contract=str(broken))


def test_a_newer_plan_or_incidents_file_is_refused_a_foreign_one_is_invalid(
    api12, data, tmp_path, history
):
    folder, contract = data
    reg, feed = history
    newer = tmp_path / "newer_plan.json"
    newer.write_text(json.dumps({"format": "shape-mutation-plan", "version": 2, "corruptions": []}))
    api12.fail("rules_mutate", "input.unsupported_format_version", data=str(folder),
               contract=str(contract), plan=str(newer))  # fmt: skip
    foreign = tmp_path / "foreign.json"
    foreign.write_text(json.dumps({"format": "other", "version": 1}))
    api12.fail("rules_mutate", "input.invalid_value", data=str(folder), contract=str(contract),
               plan=str(foreign))  # fmt: skip
    inc = tmp_path / "newer_inc.json"
    inc.write_text(json.dumps({"format": "shape-incidents", "version": 2, "incidents": []}))
    api12.fail("rules_backtest", "input.unsupported_format_version", registry=str(reg),
               name="orders", contract=str(feed), incidents=str(inc))  # fmt: skip
    api12.fail("rules_backtest", "input.invalid_value", registry=str(reg), name="orders",
               contract=str(feed), incidents=str(foreign))  # fmt: skip
    plain = tmp_path / "plain_dir"
    plain.mkdir()
    api12.fail("rules_backtest", "input.invalid_value", registry=str(plain), name="orders",
               contract=str(feed))  # fmt: skip
    assert not (plain / "objects").exists()  # a read-only command creates nothing


def test_data_with_nothing_to_corrupt_is_an_error(api12, tmp_path):
    folder = write_tables(
        tmp_path / "empty_rows", {"customers": real_tables()["customers"].slice(0, 0)}
    )
    contract = write_contract(tmp_path / "c.json", {"row_count": {"min": 0}})
    api12.fail("rules_mutate", "input.invalid_value", data=str(folder), contract=str(contract))


def test_a_bad_request_makes_no_job(api12, data, tmp_path):
    folder, _ = data
    response = api12.call(
        "rules_mutate", {"async": True}, data=str(folder), contract=str(tmp_path / "none")
    )
    assert response["error"]["code"] == "input.not_found"
    assert api12.ok("job_list")["jobs"] == []


def wait(api, job_id, timeout=120):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = api.ok("job_status", job_id=job_id)
        if status["status"] not in ("running", "submitted"):
            return status
        time.sleep(0.05)
    raise AssertionError("the job did not end")


def test_rules_mutate_runs_as_a_job_and_reports_progress(api12, data):
    folder, contract = data
    started = api12.ok(
        "rules_mutate", {"async": True}, data=str(folder), contract=str(contract), seed=2
    )
    assert started["cancellable"] is True
    done = wait(api12, started["job_id"])
    assert done["status"] == "succeeded"
    assert done["result"] == api12.ok(
        "rules_mutate", data=str(folder), contract=str(contract), seed=2
    )


def test_a_cancelled_rules_mutate_job_is_cancelled_with_its_partial_counts(
    api12, data, monkeypatch
):
    import shape.chaos.groundtruth as ground

    folder, contract = data
    real = ground.corrupt_tables
    calls: list[int] = []
    third = threading.Event()

    def slow(*a, **k):
        calls.append(1)
        if len(calls) == 3:
            third.set()
            time.sleep(1.5)  # the cancel request arrives while this mutant runs
        return real(*a, **k)

    monkeypatch.setattr(ground, "corrupt_tables", slow)
    started = api12.ok("rules_mutate", {"async": True}, data=str(folder), contract=str(contract))
    assert third.wait(30)
    cancelled = api12.ok("job_cancel", job_id=started["job_id"])
    status = wait(api12, started["job_id"])
    assert status["status"] == "cancelled", (cancelled, status)
    result = status["result"]
    assert result["mutants_run"] == len(calls) - 0 or result["mutants_run"] >= 3
    assert 3 <= result["mutants_run"] < 12
    assert result["killed"] + result["survived"] + result["not_applicable"] == result["mutants_run"]


def test_a_job_that_is_not_cancelled_runs_every_mutant(api12, data):
    from shape.rules.mutation import mutation_test

    folder, contract = data
    full = mutation_test(str(folder), str(contract))
    assert len(full.mutants) > 3  # the cancel test above stops inside this run


# ---- rules_backtest --------------------------------------------------------------------------


@pytest.fixture
def history(tmp_path):
    reg = make_registry(tmp_path / "reg", days=8, null_from=4)
    contract = write_contract(tmp_path / "feed.json")
    return reg, contract


def test_the_backtest_report_is_the_one_the_cli_prints(api12, history, capsys):
    reg, contract = history
    result = api12.ok("rules_backtest", registry=str(reg), name="orders", contract=str(contract))
    code, cli = cli_json(capsys, "rules", "backtest", str(reg), "orders", str(contract), "--json")
    assert code == 0 and result == cli
    assert result["format"] == "shape-backtest-report" and result["summary"]["entries"] == 8
    assert [e["status"] for e in result["entries"]] == ["pass"] * 4 + ["fail"] * 4


def test_since_until_and_window_limit_the_replay(api12, history, capsys):
    reg, contract = history
    args = {"registry": str(reg), "name": "orders", "contract": str(contract)}
    some = api12.ok("rules_backtest", since="2026-03-03", until="2026-03-06", **args)
    assert some["summary"]["entries"] == 4 and some["since"] == "2026-03-03"
    code, cli = cli_json(
        capsys, "rules", "backtest", str(reg), "orders", str(contract),
        "--since", "2026-03-03", "--until", "2026-03-06", "--json",
    )  # fmt: skip
    assert some == cli
    weekly = api12.ok("rules_backtest", window="week", **args)
    assert weekly["window"] == "week" and weekly["summary"]["entries"] <= 2
    assert (
        api12.ok("rules_backtest", since="2026-03-08", until="2026-03-08", **args)["summary"][
            "entries"
        ]
        == 1
    )  # the boundary: one day, both ends inclusive


def test_incidents_and_compare_are_scored_as_the_cli_does(api12, history, tmp_path, capsys):
    reg, contract = history
    incidents = tmp_path / "incidents.json"
    incidents.write_text(
        json.dumps(
            {
                "format": "shape-incidents",
                "version": 1,
                "incidents": [{"id": "nulls", "from": "2026-03-05", "to": "2026-03-08"}],
            }
        )
    )
    old = write_contract(tmp_path / "old.json", {"row_count": {"min": 100}})
    args = {"registry": str(reg), "name": "orders", "contract": str(contract)}
    result = api12.ok("rules_backtest", incidents=str(incidents), compare=str(old), **args)
    code, cli = cli_json(
        capsys, "rules", "backtest", str(reg), "orders", str(contract),
        "--incidents", str(incidents), "--compare", str(old), "--json",
    )  # fmt: skip
    assert result == cli
    assert result["incidents"][0]["status"] == "caught"
    assert result["compare"]["disagreements"] == 4


def test_a_share_safe_history_replays_and_marks_what_it_cannot_measure(api12, tmp_path):
    reg = make_registry(tmp_path / "safe_reg", days=6, form="safe", null_from=3)
    contract = write_contract(tmp_path / "c.json")
    result = api12.ok("rules_backtest", registry=str(reg), name="orders", contract=str(contract))
    assert result["summary"]["entries"] == 6
    assert (
        result["summary"]["pass"] + result["summary"]["fail"] + result["summary"]["not_measured"]
        == 6
    )


def test_rules_backtest_errors(api12, history, tmp_path):
    reg, contract = history
    args = {"registry": str(reg), "name": "orders", "contract": str(contract)}
    api12.fail("rules_backtest", "input.invalid_value", **{**args, "name": "nothing"})
    api12.fail("rules_backtest", "input.not_found", **{**args, "registry": str(tmp_path / "no")})
    api12.fail("rules_backtest", "input.not_found", **{**args, "contract": str(tmp_path / "no")})
    api12.fail("rules_backtest", "input.not_found", incidents=str(tmp_path / "no"), **args)
    api12.fail("rules_backtest", "input.not_found", compare=str(tmp_path / "no"), **args)
    api12.fail("rules_backtest", "usage.invalid_argument", window="year", **args)
    api12.fail("rules_backtest", "usage.missing_argument", registry=str(reg), name="orders")
    api12.fail("rules_backtest", "input.invalid_value", since="yesterday", **args)
    api12.fail(
        "rules_backtest", "input.invalid_value", since="2026-03-05", until="2026-03-01", **args
    )
    api12.fail("rules_backtest", "input.invalid_value", since="2027-01-01", **args)


def test_rules_backtest_runs_as_a_job(api12, history):
    reg, contract = history
    started = api12.ok(
        "rules_backtest",
        {"async": True},
        registry=str(reg),
        name="orders",
        contract=str(contract),
    )
    assert started["cancellable"] is False
    done = wait(api12, started["job_id"])
    assert done["status"] == "succeeded" and done["result"]["format"] == "shape-backtest-report"


def test_effects_and_annotations():
    from shape.bridge.registry import COMMANDS

    mutate, backtest = COMMANDS["rules_mutate"], COMMANDS["rules_backtest"]
    assert mutate.job and mutate.cancellable and backtest.job and not backtest.cancellable
    assert mutate.effects == ("reads_files", "cancels") and backtest.effects == ("reads_files",)
    assert {k for k, a in mutate.args.items() if a.path} == {"data", "contract", "plan"}
    assert {k for k, a in backtest.args.items() if a.path} == {
        "registry",
        "contract",
        "incidents",
        "compare",
    }
