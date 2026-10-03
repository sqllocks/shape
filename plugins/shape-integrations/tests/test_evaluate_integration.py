"""Item 5 with SDMetrics and Anonymeter installed. Both are informational, never gates."""

from __future__ import annotations

import json

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from shape_integrations import evaluation
from shape_integrations.testing import write_tables

pytestmark = pytest.mark.integration


def run(*argv: str) -> int:
    from shape.plugins.cli import run_command
    from shape.plugins.host import default_host

    return run_command(default_host(), "evaluate", list(argv))


@pytest.fixture
def dirs(tmp_path):
    return (
        str(write_tables(tmp_path / "real", seed=1)),
        str(write_tables(tmp_path / "synth", seed=2)),
        str(write_tables(tmp_path / "control", seed=3)),
    )


class TestSdmetrics:
    @pytest.fixture(autouse=True)
    def _library(self):
        pytest.importorskip("sdmetrics", reason="needs the 'sdmetrics' extra")

    def test_the_report_is_written_and_valid(self, dirs, tmp_path):
        out = tmp_path / "r.json"
        assert run("sdmetrics", dirs[0], dirs[1], "-o", str(out)) == 0
        r = evaluation.read_report(out)
        import sdmetrics

        assert (r["tool"], r["tool_version"]) == ("sdmetrics", sdmetrics.__version__)
        res = r["results"]
        assert res["tables"] == ["orders", "people"]
        assert 0.0 <= res["overall_score"] <= 1.0
        assert {s["table"] for s in res["column_shapes"]} == {"orders", "people"}
        assert all(0.0 <= s["score"] <= 1.0 for s in res["column_shapes"])

    def test_json_prints_the_same_report(self, dirs, tmp_path, capsys):
        out = tmp_path / "r.json"
        assert run("sdmetrics", dirs[0], dirs[1], "-o", str(out), "--json") == 0
        assert json.loads(capsys.readouterr().out) == json.loads(out.read_text())

    def test_without_output_flags_a_summary_is_printed(self, dirs, capsys):
        assert run("sdmetrics", dirs[0], dirs[1]) == 0
        assert "SDMetrics quality score" in capsys.readouterr().out

    def test_tables_restricts_the_evaluation(self, dirs, tmp_path, capsys):
        assert run("sdmetrics", dirs[0], dirs[1], "--tables", "people", "--json") == 0
        res = json.loads(capsys.readouterr().out)["results"]
        assert res["tables"] == ["people"]

    def test_a_shifted_synthetic_table_scores_lower_than_a_matching_one(self, tmp_path, capsys):
        real = write_tables(tmp_path / "r", seed=1, extra=False)
        same = write_tables(tmp_path / "same", seed=1, extra=False)
        far = write_tables(tmp_path / "far", seed=2, shift=200.0, extra=False)
        run("sdmetrics", str(real), str(same), "--json")
        s_same = json.loads(capsys.readouterr().out)["results"]["overall_score"]
        run("sdmetrics", str(real), str(far), "--json")
        s_far = json.loads(capsys.readouterr().out)["results"]["overall_score"]
        assert s_same == pytest.approx(1.0)
        assert s_far < s_same

    def test_unsupported_columns_are_listed_not_fatal(self, dirs, tmp_path, capsys):
        for d in (dirs[0], dirs[1]):
            t = pq.read_table(f"{d}/people.parquet")
            t = t.append_column("tags", pa.array([["a"]] * t.num_rows))
            pq.write_table(t, f"{d}/people.parquet")
        assert run("sdmetrics", dirs[0], dirs[1], "--tables", "people", "--json") == 0
        res = json.loads(capsys.readouterr().out)["results"]
        assert res["skipped_columns"] == {"people": ["tags"]}

    def test_an_empty_table_exits_2(self, dirs, capsys):
        t = pq.read_table(f"{dirs[1]}/people.parquet").slice(0, 0)
        pq.write_table(t, f"{dirs[1]}/people.parquet")
        assert run("sdmetrics", dirs[0], dirs[1]) == 2
        assert "no rows" in capsys.readouterr().err


class TestAnonymeter:
    @pytest.fixture(autouse=True)
    def _library(self):
        pytest.importorskip("anonymeter", reason="needs the 'anonymeter' extra")

    def test_all_attacks_by_default(self, dirs, tmp_path):
        out = tmp_path / "r.json"
        assert run("anonymeter", dirs[0], dirs[1], "--control", dirs[2], "-o", str(out)) == 0
        r = evaluation.read_report(out)
        assert r["tool"] == "anonymeter"
        res = r["results"]
        assert res["attacks"] == ["singling-out", "linkability", "inference"]
        people = res["tables"]["people"]
        assert set(people) == {"rows", "singling_out", "linkability", "inference"}
        assert set(people["inference"]["secrets"]) == {"age", "city", "income"}
        for outcome in (people["singling_out"], people["linkability"]):
            assert 0.0 <= outcome["attack_rate"]["value"] <= 1.0
            assert outcome["n_attacks"] == 300  # fewer rows than the 500 default
            lo, hi = outcome["risk"]["ci"]
            assert lo <= outcome["risk"]["value"] <= hi

    def test_attacks_selects_which_run(self, dirs, capsys):
        args = ["anonymeter", dirs[0], dirs[1], "--control", dirs[2], "--json"]
        assert run(*args, "--attacks", "linkability") == 0
        res = json.loads(capsys.readouterr().out)["results"]
        assert res["attacks"] == ["linkability"]
        assert set(res["tables"]["people"]) == {"rows", "linkability"}

    def test_the_singling_out_attack_is_seeded(self, dirs, capsys):
        args = ["anonymeter", dirs[0], dirs[1], "--control", dirs[2], "--attacks", "singling-out"]
        run(*args, "--json")
        a = json.loads(capsys.readouterr().out)["results"]["tables"]["people"]["singling_out"]
        run(*args, "--json")
        b = json.loads(capsys.readouterr().out)["results"]["tables"]["people"]["singling_out"]
        # The attack queries are seeded; Anonymeter's baseline guesses are not.
        for key in ("n_attacks", "n_success", "attack_rate"):
            assert a[key] == b[key]

    def test_a_single_column_table_skips_linkability_and_inference(self, tmp_path, capsys):
        for name, seed in (("r", 1), ("s", 2), ("c", 3)):
            d = tmp_path / name
            d.mkdir()
            pq.write_table(pa.table({"x": list(range(seed, seed + 60))}), d / "t.parquet")
        args = ["anonymeter", str(tmp_path / "r"), str(tmp_path / "s")]
        assert run(*args, "--control", str(tmp_path / "c"), "--json") == 0
        t = json.loads(capsys.readouterr().out)["results"]["tables"]["t"]
        assert t["linkability"] == {"skipped": "needs at least 2 columns"}
        assert t["inference"] == {"skipped": "needs at least 2 columns"}

    def test_too_few_rows_exits_2(self, dirs, capsys):
        t = pq.read_table(f"{dirs[2]}/people.parquet").slice(0, 1)
        pq.write_table(t, f"{dirs[2]}/people.parquet")
        assert run("anonymeter", dirs[0], dirs[1], "--control", dirs[2]) == 2
        assert "at least 2" in capsys.readouterr().err
