"""P4-09: the fidelity report (``shape.generation.report``) and its ``shape.reports`` formats."""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pyarrow as pa
import pytest
from scipy import special, stats

from shape.generation.report import (
    Thresholds,
    compare_column,
    compare_table,
    compare_tables,
    render_report,
)
from shape.generation.report.compare import chi2_sf, gammaincc, ks_statistic
from shape.plugins import kit
from shape.plugins.host import default_host

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from refengine_name import names_refengine  # noqa: E402

RNG = np.random.default_rng(7)


def _t(**cols):
    return pa.table(cols)


def _table(n=200):
    return _t(
        id=list(range(n)),
        amount=RNG.normal(50, 5, n),
        kind=["a", "b", "c", "d"] * (n // 4),
        when=pa.array(np.arange(n).astype("datetime64[D]").astype("datetime64[us]")),
    )


# --- statistics against SciPy ---------------------------------------------------------------


@pytest.mark.parametrize("a", [0.5, 1, 2.5, 10, 100, 1_000, 50_000, 400_000])
@pytest.mark.parametrize("ratio", [0.2, 0.9, 1.0, 1.05, 1.5, 4.0])
def test_gammaincc_matches_scipy(a, ratio):
    x = a * ratio
    want = float(special.gammaincc(a, x))
    assert gammaincc(a, x) == pytest.approx(want, rel=1e-6, abs=1e-12)


def test_gammaincc_edges():
    assert gammaincc(3.0, 0.0) == 1.0
    assert gammaincc(3.0, math.inf) == 0.0


@pytest.mark.parametrize(
    ("stat", "df"), [(0.5, 1), (3.84, 1), (9.49, 4), (120.0, 20), (5e4, 49_999)]
)
def test_chi2_sf_matches_scipy(stat, df):
    assert chi2_sf(stat, df) == pytest.approx(float(stats.chi2.sf(stat, df)), rel=1e-5, abs=1e-12)


@pytest.mark.parametrize("shift", [0.0, 0.3, 2.0])
def test_ks_statistic_matches_scipy(shift):
    a = RNG.normal(0, 1, 500)
    b = RNG.normal(shift, 1, 700)
    assert ks_statistic(a, b) == pytest.approx(stats.ks_2samp(a, b).statistic, abs=1e-12)


def test_ks_statistic_with_ties():
    a = np.array([1, 1, 2, 2, 3, 3, 3])
    b = np.array([1, 2, 2, 2, 3, 4])
    assert ks_statistic(a, b) == pytest.approx(stats.ks_2samp(a, b).statistic, abs=1e-12)


# --- scoring --------------------------------------------------------------------------------


def test_identical_tables_score_100():
    t = _table()
    rep = compare_tables({"t": t}, {"t": t})
    assert rep.overall_score == pytest.approx(100.0)
    assert rep.passed()


def test_a_missing_column_scores_zero_and_lowers_the_table():
    t = _table()
    full = compare_tables({"t": t}, {"t": t})
    part = compare_tables({"t": t}, {"t": t.drop_columns(["amount"])})
    tf = part.tables["t"]
    assert tf.columns["amount"].score == 0.0 and not tf.columns["amount"].present
    assert tf.missing_columns == ("amount",)
    assert tf.score == pytest.approx(full.tables["t"].score * 3 / 4)
    assert not part.passed()
    assert any("amount" in f for f in part.failures())


def test_a_missing_table_scores_zero_and_fails():
    t = _table()
    rep = compare_tables({"a": t, "b": t}, {"a": t})
    assert rep.tables["b"].score == 0.0 and rep.tables["b"].present is False
    assert rep.missing_tables == ("b",)
    assert rep.overall_score == pytest.approx(50.0)
    assert not rep.passed(Thresholds(min_overall=0, min_table=0))  # missing is a failure itself


def test_extra_columns_and_tables_do_not_change_the_score():
    t = _table()
    base = compare_tables({"t": t}, {"t": t})
    more = compare_tables(
        {"t": t}, {"t": t.append_column("extra", pa.array(range(t.num_rows))), "u": t}
    )
    assert more.overall_score == base.overall_score
    assert more.tables["t"].extra_columns == ("extra",)
    assert more.extra_tables == ("u",)
    assert more.passed()


def test_an_empty_reference_fails():
    t = _table()
    for ref in ({}, {"t": t.slice(0, 0)}, {"t": pa.table({})}):
        rep = compare_tables(ref, {"t": t})
        assert rep.overall_score == 0.0
        assert not rep.passed(Thresholds(min_overall=0, min_table=0, min_column=None))
        assert rep.failures()


def test_an_empty_synthetic_table_scores_low_and_fails():
    t = _table()
    rep = compare_tables({"t": t}, {"t": t.slice(0, 0)})
    assert rep.overall_score < 85 and not rep.passed()


def test_kind_mismatch_loses_the_kind_points():
    real = _t(x=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    synth = _t(x=["a", "b", "c", "d", "e", "f"])
    c = compare_column("x", real["x"], synth["x"])
    assert c.dtype_match is False and c.score < 50


def test_numbers_and_dates_held_as_text_are_numbers_and_dates():
    r = compare_column("x", ["1", "2", "3", "4", "5", "6"], [1, 2, 3, 4, 5, 6])
    assert r.dtype_match and r.ks_statistic == 0.0
    d = compare_column(
        "d",
        ["2024-01-01", "2024-02-03", "2024-03-04"] * 3,
        pa.array(np.array(["2024-01-01", "2024-02-03", "2024-03-04"] * 3, dtype="datetime64[us]")),
    )
    assert d.dtype_match and d.mean_delta == 0.0


def test_datetimes_in_different_units_score_as_equal():
    ms = pa.array(np.arange(50).astype("datetime64[s]").astype("datetime64[ms]"))
    ns = pa.array(np.arange(50).astype("datetime64[s]").astype("datetime64[ns]"))
    c = compare_column("t", ms, ns)
    assert c.score == pytest.approx(100.0) and c.ks_statistic == 0.0


def test_nan_counts_as_null():
    a = pa.array([1.0, float("nan"), 3.0, 4.0, 5.0, 6.0, 7.0])
    b = pa.array([1.0, None, 3.0, 4.0, 5.0, 6.0, 7.0])
    assert compare_column("x", a, b).null_rate_delta == 0.0


def test_dictionary_columns_compare_by_value():
    plain = pa.array(["a", "b", "a", "c"] * 10)
    c = compare_column("x", plain, plain.dictionary_encode())
    assert c.score == pytest.approx(100.0)


def test_categories_of_different_kinds_never_overlap():
    c = compare_column("x", pa.array([True, False] * 5), pa.array(["true", "false"] * 5))
    assert c.value_overlap == 0.0


# --- thresholds, the dict, the formats ---------------------------------------------------------


def test_thresholds_decide_the_verdict():
    real = _table()
    synth = real.set_column(1, "amount", pa.array(RNG.normal(60, 5, real.num_rows)))
    rep = compare_tables({"t": real}, {"t": synth})
    score = rep.overall_score
    assert 0 < score < 100
    assert rep.passed(Thresholds(min_overall=score - 1, min_table=score - 1))
    assert not rep.passed(Thresholds(min_overall=score + 1, min_table=0))
    assert not rep.passed(Thresholds(min_overall=0, min_table=score + 1))
    worst = min(c.score for c in rep.tables["t"].columns.values())
    assert not rep.passed(Thresholds(0, 0, min_column=worst + 0.01))
    assert rep.passed(Thresholds(0, 0, min_column=worst - 0.01))


def test_failing_columns_lists_the_lowest_first():
    real = _table()
    synth = real.set_column(1, "amount", pa.array(RNG.normal(90, 5, real.num_rows)))
    rows = compare_tables({"t": real}, {"t": synth}).failing_columns(95.0)
    assert [r[1] for r in rows][0] == "amount"
    assert [r[2] for r in rows] == sorted(r[2] for r in rows)


def test_to_dict_is_strict_json():
    real = _t(one=[7.0, None, None, None, None, None], few=["a", "b", "a", "b", "a", "b"])
    out = compare_tables({"t": real}, {"t": real}).to_dict()
    json.dumps(out, allow_nan=False)  # NaN std of one value becomes null
    assert set(out) >= {"overall_score", "passed", "failures", "thresholds", "tables"}


def _report(passed=True):
    t = _table(40)
    synth = t if passed else t.drop_columns(["amount"])
    return compare_tables({"t<script>": t}, {"t<script>": synth}).to_dict()


@pytest.mark.parametrize("fmt", ["json", "md", "html"])
def test_formats_are_builtin_plugins_that_conform(fmt):
    plugin = default_host().get("shape.reports", fmt)
    kit.check_report_format(plugin, _report())
    assert plugin.extension == {"json": ".json", "md": ".md", "html": ".html"}[fmt]


def test_json_report_round_trips():
    rep = _report(False)
    assert json.loads(render_report(rep, "json")) == json.loads(json.dumps(rep))


def test_markdown_report_names_the_failures():
    text = render_report(_report(False), "md").decode()
    assert text.startswith("# Fidelity report") and "FAIL" in text
    assert "## Failures" in text and "amount" in text and "missing" in text


def test_html_report_is_self_contained_and_escaped():
    page = render_report(_report(False), "html").decode()
    assert page.startswith("<!DOCTYPE html>")
    assert "<script" not in page and "src=" not in page and "http" not in page
    assert "t&lt;script&gt;" in page
    assert "FAIL" in page


def test_render_report_rejects_an_unknown_format():
    with pytest.raises(Exception, match="nope"):
        render_report(_report(), "nope")


def test_reports_never_name_the_reference_implementation():
    for fmt in ("json", "md", "html"):
        assert not names_refengine(render_report(_report(), fmt))


def test_compare_table_of_a_table_without_a_counterpart():
    tf = compare_table("t", _table(), None)
    assert tf.score == 0.0 and not tf.present and len(tf.missing_columns) == 4


def test_default_pass_marks():
    assert Thresholds() == Thresholds(min_overall=85.0, min_table=70.0, min_column=None)
