"""W3-04: ``shape explain`` (deterministic narrative of a diff or drift report) and the notebook
display (``_repr_html_`` / ``_repr_markdown_``) with safe values only."""

import json
import re

import pytest

import shape
from shape.cli.main import main
from shape.contracts.v1 import CheckResult, DiffResult
from shape.fidelity.tier3 import ColumnDriftResult, DriftReport
from shape.report.explain import explain

SECRET_EMAIL = "zelda.fitzgerald@example.com"
SECRET_CAT = "ZorgonPrime"


def _change(column, kind, baseline, current, severity="medium", score=0.5):
    return {
        "column": column,
        "kind": kind,
        "baseline": baseline,
        "current": current,
        "severity": severity,
        "score": score,
    }


def _diff():
    return DiffResult(
        drifted=True,
        changes=[
            _change("amount", "mean_shift", 10.0, 42.5, "high", 0.8),
            _change("amount", "null_rate_change", 0.0, 0.2, "medium", 0.2),
            _change(
                "email", "range_change", {"min": SECRET_EMAIL, "max": "z"}, {"min": "a", "max": "b"}
            ),
            _change("email", "new_categorical_values", ["a"], ["a", SECRET_EMAIL], "medium", 0.1),
            _change(
                "tier", "new_categorical_values", ["gold"], ["gold", SECRET_CAT], "medium", 0.1
            ),
        ],
    )


def _profile():
    rows = [
        {"email": f"user{i}@example.com", "amount": float(i), "tier": "gold"} for i in range(60)
    ]
    return shape.profile({"t": rows})


def test_explain_is_deterministic_and_structured():
    a, b = explain(_diff()), explain(_diff())
    assert a.text == b.text
    assert a.to_dict() == b.to_dict()
    d = a.to_dict()
    assert d["format"] == "shape.explain" and d["version"] == 1
    assert d["kind"] == "diff" and d["drifted"] is True
    assert d["counts"]["changes"] == 5 and d["counts"]["columns"] == 3
    amount = next(c for c in d["columns"] if c["column"] == "amount")
    assert {x["kind"] for x in amount["changes"]} == {"mean_shift", "null_rate_change"}
    assert "amount" in a.text and "mean" in a.text
    json.dumps(d, allow_nan=False)


def test_explain_orders_most_severe_first_and_names_causes():
    d = explain(_diff()).to_dict()
    assert d["columns"][0]["column"] == "amount"
    assert d["likely_causes"]
    assert all(isinstance(c, str) for c in d["likely_causes"])


def test_no_change_is_said_plainly():
    r = explain(DiffResult(drifted=False, changes=[]))
    assert "no change" in r.text.lower()
    assert r.to_dict()["counts"]["changes"] == 0


def test_default_deny_withholds_values_without_profiles():
    r = explain(_diff())
    blob = r.text + json.dumps(r.to_dict())
    assert SECRET_EMAIL not in blob and SECRET_CAT not in blob


def test_classified_columns_never_print_values():
    r = explain(_diff(), classified=["amount"])
    amount = next(c for c in r.to_dict()["columns"] if c["column"] == "amount")
    assert amount["withheld"] is True
    assert "42.5" not in r.text
    assert all("baseline" not in x and "current" not in x for x in amount["changes"])


def test_pii_columns_found_from_profile_are_withheld():
    p = _profile()
    r = explain(_diff(), baseline=p, current=p)
    d = r.to_dict()
    email = next(c for c in d["columns"] if c["column"] == "email")
    assert email["withheld"] is True
    assert SECRET_EMAIL not in r.text + json.dumps(d)


def test_drift_report_explained():
    rep = DriftReport(
        columns={
            "age": ColumnDriftResult("age", 0.7, 0.4, 0.001, True, "ks", 0.31),
            "city": ColumnDriftResult("city", 0.0, 1.0, 0.9, False, "chi2", 0.01),
        },
        drifted_columns=["age"],
        drift_fraction=0.5,
        overall_drift_score=0.35,
    )
    r = explain({"method": "ks+chi2+psi", "drifted": True, "tables": {"t": rep.to_dict()}})
    d = r.to_dict()
    assert d["kind"] == "drift" and d["drifted"] is True
    assert d["counts"]["columns"] == 1
    assert "age" in r.text and "city" not in r.text.split("Likely")[0]


def test_explain_rejects_unknown_input():
    with pytest.raises(ValueError, match="diff"):
        explain({"hello": 1})


# -- CLI -------------------------------------------------------------------------------------


def _write_diff(tmp_path):
    p = tmp_path / "diff.json"
    p.write_text(json.dumps(_diff().to_dict()))
    return p


def test_cli_explain_text_and_json(tmp_path, capsys):
    p = _write_diff(tmp_path)
    assert main(["explain", str(p)]) == 0
    out1 = capsys.readouterr().out
    assert main(["explain", str(p)]) == 0
    assert capsys.readouterr().out == out1
    assert SECRET_EMAIL not in out1 and SECRET_CAT not in out1
    assert main(["explain", str(p), "--json"]) == 0
    d = json.loads(capsys.readouterr().out)["payload"]  # W1-14: under payload of shape-result
    assert d["format"] == "shape.explain"


def test_cli_explain_classified_flag(tmp_path, capsys):
    p = _write_diff(tmp_path)
    assert main(["explain", str(p), "--json", "--classified", "amount"]) == 0
    d = json.loads(capsys.readouterr().out)
    assert next(c for c in d["columns"] if c["column"] == "amount")["withheld"] is True


def test_cli_explain_bad_input_exits_2(tmp_path, capsys):
    assert main(["explain", str(tmp_path / "missing.json")]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert main(["explain", str(bad)]) == 2
    other = tmp_path / "other.json"
    other.write_text('{"a": 1}')
    assert main(["explain", str(other)]) == 2
    capsys.readouterr()


def test_shape_explain_module_stays_removed():
    with pytest.raises(ModuleNotFoundError):
        __import__("shape.explain")


# -- notebook display --------------------------------------------------------------------------

_EVIL = '<script>alert("x")</script>|`'


def _no_markup(html_text):
    assert "<script" not in html_text.lower()
    assert "alert(" not in re.sub(r"&[a-z#0-9]+;", "", html_text) or "&lt;script" in html_text


def test_diff_repr_html_and_markdown_safe_and_small():
    d = _diff()
    d.changes.append(_change(_EVIL, "dtype_change", "int", _EVIL, "high", 1.0))
    h, m = d._repr_html_(), d._repr_markdown_()
    assert "<script" not in h.lower() and "&lt;script&gt;" in h
    assert SECRET_EMAIL not in h + m and SECRET_CAT not in h + m
    assert "<script" not in m.lower()
    assert len(h) < 40_000 and len(m) < 20_000


def test_diff_repr_caps_rows():
    d = DiffResult(True, [_change(f"c{i}", "null_rate_change", 0.0, 0.5) for i in range(500)])
    h = d._repr_html_()
    assert h.count("<tr") <= 60
    assert "more" in h


def test_profile_repr_hides_classified_values():
    p = _profile()
    h, m = p._repr_html_(), p._repr_markdown_()
    assert "email" in h and "amount" in h
    for text in (h, m):
        assert "user1@example.com" not in text and "user59@example.com" not in text
    assert len(h) < 40_000


def test_profile_repr_escapes_names():
    rows = [{_EVIL: float(i)} for i in range(30)]
    p = shape.profile({"t": rows})
    h, m = p._repr_html_(), p._repr_markdown_()
    assert "<script" not in h.lower()
    assert "<script" not in m.lower()


def test_check_result_repr_withholds_value_rules():
    r = CheckResult(
        False,
        [
            {"column": "email", "rule": "max", "expected": "m", "observed": SECRET_EMAIL},
            {"column": "a", "rule": "max_null_rate", "expected": 0.1, "observed": 0.4},
            {"column": _EVIL, "rule": "dtype", "expected": "int", "observed": _EVIL},
        ],
    )
    h, m = r._repr_html_(), r._repr_markdown_()
    assert SECRET_EMAIL not in h + m
    assert "0.4" in h and "0.4" in m
    assert "<script" not in h.lower() and "<script" not in m.lower()
    ok = CheckResult(True, [])
    assert "pass" in ok._repr_html_().lower()


def test_drift_report_repr():
    rep = DriftReport(
        columns={"<b>x</b>": ColumnDriftResult("<b>x</b>", 0.7, 0.4, 0.001, True, "ks", 0.31)},
        drifted_columns=["<b>x</b>"],
        drift_fraction=1.0,
        overall_drift_score=0.7,
    )
    h, m = rep._repr_html_(), rep._repr_markdown_()
    assert "<b>x</b>" not in h and "&lt;b&gt;x&lt;/b&gt;" in h
    assert "<b>" not in m.replace("&lt;b&gt;", "")
