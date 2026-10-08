"""W3-01: the ``shape rules`` command line."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.csv as pacsv
import pytest

import shape
from shape.cli.main import main
from shape.registry import LocalRegistry
from shape.rules import mutation_test

CONTRACT = {
    "columns": {
        "order_id": {"unique": True, "nullable": False},
        "status": {"allowed_values": ["new", "paid", "shipped"]},
    }
}


def orders(n: int = 300) -> pa.Table:
    return pa.table(
        {
            "order_id": list(range(1, n + 1)),
            "customer_id": [1 + i % 30 for i in range(n)],
            "status": [["new", "paid", "shipped"][i % 3] for i in range(n)],
            "amount": [round(10 + (i % 17) * 3.5, 2) for i in range(n)],
        }
    )


@pytest.fixture()
def work(tmp_path: Path) -> Path:
    pacsv.write_csv(orders(), tmp_path / "orders.csv")
    (tmp_path / "contract.json").write_text(json.dumps(CONTRACT))
    (tmp_path / "empty.json").write_text("{}")
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str, str]:
    rc = main(list(args))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_mutate_json_equals_the_api_report(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc, out, _ = run(
        capsys,
        "rules",
        "mutate",
        str(work / "orders.csv"),
        str(work / "contract.json"),
        "--seed",
        "5",
        "--json",
    )
    assert rc == 0
    api = mutation_test(work / "orders.csv", CONTRACT, seed=5).to_dict()
    assert json.loads(out)["payload"] == api  # W1-14: the report's format clashes, so payload


def test_mutate_text_lists_the_survivors_first(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    rc, out, _ = run(capsys, "rules", "mutate", str(work / "orders.csv"), str(work / "empty.json"))
    assert rc == 0
    assert out.splitlines()[0].startswith("mutation score 0.0%")
    assert out.index("SURVIVED") < out.index("by kind")
    rc, out, _ = run(
        capsys, "rules", "mutate", str(work / "orders.csv"), str(work / "contract.json")
    )
    assert out.index("SURVIVED") < out.index("killed (")


def test_mutate_writes_the_report_with_o(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    target = work / "report.json"
    rc, _, _ = run(
        capsys,
        "rules",
        "mutate",
        str(work / "orders.csv"),
        str(work / "contract.json"),
        "-o",
        str(target),
    )
    assert rc == 0
    doc = json.loads(target.read_text())
    assert doc["format"] == "shape-mutation-report" and doc["version"] == 1


def test_min_score_decides_between_exit_0_and_1(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ("rules", "mutate", str(work / "orders.csv"))
    rc, _, err = run(capsys, *args, str(work / "empty.json"), "--min-score", "0.1")
    assert rc == 1 and "below --min-score" in err
    rc, _, _ = run(capsys, *args, str(work / "empty.json"), "--min-score", "0")
    assert rc == 0  # a score of 0 is not below 0
    score = mutation_test(work / "orders.csv", CONTRACT).score
    assert score is not None
    rc, _, _ = run(capsys, *args, str(work / "contract.json"), "--min-score", str(score))
    assert rc == 0
    rc, _, _ = run(
        capsys, *args, str(work / "contract.json"), "--min-score", str(min(1.0, score + 0.001))
    )
    assert rc == 1


@pytest.mark.parametrize("bad", ["1.5", "-0.1"])
def test_min_score_out_of_range_is_exit_2(
    work: Path, capsys: pytest.CaptureFixture[str], bad: str
) -> None:
    rc, _, err = run(
        capsys,
        "rules",
        "mutate",
        str(work / "orders.csv"),
        str(work / "contract.json"),
        f"--min-score={bad}",
    )
    assert rc == 2 and "--min-score" in err


def test_mutate_unusable_input_is_exit_2(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    contract = str(work / "contract.json")
    data = str(work / "orders.csv")
    rc, _, err = run(capsys, "rules", "mutate", str(work / "nope.csv"), contract)
    assert rc == 2 and "not found" in err
    rc, _, err = run(capsys, "rules", "mutate", data, str(work / "missing.json"))
    assert rc == 2
    (work / "broken.json").write_text("{oops")
    rc, _, err = run(capsys, "rules", "mutate", data, str(work / "broken.json"))
    assert rc == 2 and "not valid JSON" in err

    def plan(corruption: dict[str, Any]) -> str:
        path = work / "plan.json"
        path.write_text(
            json.dumps({"format": "shape-mutation-plan", "version": 1, "corruptions": [corruption]})
        )
        return str(path)

    rc, _, err = run(
        capsys,
        "rules",
        "mutate",
        data,
        contract,
        "--plan",
        plan({"kind": "shred", "table": "orders", "column": "status"}),
    )
    assert rc == 2 and "unknown corruption" in err
    rc, _, err = run(
        capsys,
        "rules",
        "mutate",
        data,
        contract,
        "--plan",
        plan({"kind": "null_creep", "table": "orders", "column": "nope"}),
    )
    assert rc == 2 and "no column" in err
    rc, _, err = run(capsys, "rules", "mutate", data, contract, "--rate", "2")
    assert rc == 2 and "rate" in err


def test_mutate_with_a_plan_and_diff(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    plan = work / "plan.json"
    plan.write_text(
        json.dumps(
            {
                "format": "shape-mutation-plan",
                "version": 1,
                "corruptions": [
                    {"kind": "null_creep", "table": "orders", "column": "status", "rate": 0.4}
                ],
            }
        )
    )
    rc, out, _ = run(
        capsys,
        "rules",
        "mutate",
        str(work / "orders.csv"),
        str(work / "empty.json"),
        "--plan",
        str(plan),
        "--diff",
        "--json",
    )
    doc = json.loads(out)
    assert rc == 0 and doc["diff"] is True
    (m,) = doc["mutants"]
    assert m["killed"] and any(k.startswith("drift:") for k in m["killed_by"])


@pytest.fixture()
def history(tmp_path: Path) -> Path:
    reg = LocalRegistry(tmp_path / "reg")
    for n, null_every in enumerate([0, 0, 2, 2]):
        t = pa.table({"x": [None if null_every and i % null_every == 0 else i for i in range(60)]})
        path = tmp_path / f"v{n}.shape"
        shape.save(shape.profile(t, name="t"), path)
        reg.commit("t", path.read_bytes(), {"business_date": f"2026-05-0{n + 1}"}, allow_raw=True)
    (tmp_path / "strict.json").write_text(json.dumps({"columns": {"x": {"max_null_rate": 0.1}}}))
    (tmp_path / "lax.json").write_text(json.dumps({"columns": {"x": {"max_null_rate": 0.9}}}))
    return tmp_path


def incidents(path: Path, *rows: tuple[str, str]) -> str:
    target = path / "incidents.json"
    target.write_text(
        json.dumps(
            {
                "format": "shape-incidents",
                "version": 1,
                "incidents": [{"id": i, "from": d, "note": ""} for i, d in rows],
            }
        )
    )
    return str(target)


def test_backtest_json_text_and_output(history: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg, strict = str(history / "reg"), str(history / "strict.json")
    rc, out, _ = run(capsys, "rules", "backtest", reg, "t", strict)
    assert rc == 0 and "t.x.max_null_rate" in out and "2 pass, 2 fail" in out
    target = history / "report.json"
    rc, out, _ = run(capsys, "rules", "backtest", reg, "t", strict, "--json", "-o", str(target))
    printed = json.loads(out)["payload"]  # W1-14: the report's format clashes, so payload
    assert rc == 0 and printed == json.loads(target.read_text())
    from shape.rules import backtest

    assert printed == backtest(reg, "t", strict).to_dict()


def test_fail_on_miss_exit_codes(history: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reg, strict, lax = str(history / "reg"), str(history / "strict.json"), str(history / "lax.json")
    hit = incidents(history, ("a", "2026-05-03"))
    rc, _, _ = run(
        capsys, "rules", "backtest", reg, "t", strict, "--incidents", hit, "--fail-on-miss"
    )
    assert rc == 0
    rc, out, err = run(
        capsys, "rules", "backtest", reg, "t", lax, "--incidents", hit, "--fail-on-miss"
    )
    assert rc == 1 and "missed incidents: a" in err and "missed" in out
    rc, _, _ = run(capsys, "rules", "backtest", reg, "t", lax, "--incidents", hit)
    assert rc == 0  # without --fail-on-miss a report is exit 0
    rc, _, err = run(capsys, "rules", "backtest", reg, "t", lax, "--fail-on-miss")
    assert rc == 2 and "--incidents" in err


def test_backtest_unusable_input_is_exit_2(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg, strict = str(history / "reg"), str(history / "strict.json")
    rc, _, err = run(capsys, "rules", "backtest", reg, "ghost", strict)
    assert rc == 2 and "nothing is recorded" in err
    rc, _, err = run(capsys, "rules", "backtest", reg, "t", strict, "--since", "2027-01-01")
    assert rc == 2 and "no committed version" in err
    (history / "bad.json").write_text('{"format": "shape-incidents"}')
    rc, _, err = run(
        capsys, "rules", "backtest", reg, "t", strict, "--incidents", str(history / "bad.json")
    )
    assert rc == 2 and "version" in err
    rc, _, err = run(capsys, "rules", "backtest", reg, "t", str(history / "nope.json"))
    assert rc == 2


def test_backtest_since_until_window_and_compare(
    history: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    reg, strict, lax = str(history / "reg"), str(history / "strict.json"), str(history / "lax.json")
    rc, out, _ = run(
        capsys,
        "rules",
        "backtest",
        reg,
        "t",
        strict,
        "--since",
        "2026-05-02",
        "--until",
        "2026-05-03",
        "--json",
    )
    assert [e["first_date"] for e in json.loads(out)["entries"]] == ["2026-05-02", "2026-05-03"]
    rc, out, _ = run(capsys, "rules", "backtest", reg, "t", strict, "--window", "month", "--json")
    assert [e["id"] for e in json.loads(out)["entries"]] == ["2026-05"]
    rc, out, _ = run(capsys, "rules", "backtest", reg, "t", strict, "--compare", lax, "--json")
    assert json.loads(out)["compare"]["disagreements"] == 2
    rc, out, _ = run(capsys, "rules", "backtest", reg, "t", strict, "--compare", lax)
    assert "disagree on 2 entries" in out


def test_rules_help_loads_nothing_heavy() -> None:
    code = (
        "import sys\n"
        "from shape.cli.main import main\n"
        "try:\n    main(['rules', '--help'])\nexcept SystemExit:\n    pass\n"
        "heavy = ('numpy', 'pyarrow', 'pandas', 'shape.profile')\n"
        "print('HEAVY', [m for m in heavy if m in sys.modules])\n"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip().splitlines()[-1] == "HEAVY []"
