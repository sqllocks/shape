"""Approved FRZ-01 correction: identifier forms come from the frozen release."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pandas as pd
import pyarrow as pa
import pytest

from .test_domain_verify_recorded import sample, verifier
from .test_record_baseline import recorder

PROVIDERS = ("email", "company_email", "phone_number", "uri")


def baseline_provider(monkeypatch):
    from shape.builtins.strategies import providers as p

    monkeypatch.setattr(
        p,
        "pool",
        lambda name: pa.array(["alice", "bob"] if name == "first_names" else ["smith", "jones"]),
    )
    monkeypatch.setattr(p, "_first_name", lambda ctx: pa.array(["alice", "bob"]))
    monkeypatch.setattr(p, "_last_name", lambda ctx: pa.array(["smith", "jones"]))

    def email(ctx, spec):
        domains = ["mail.test"] if spec.get("domains") == "realistic" else ["example.org"]
        return p.kernel_ops.template_strings(
            ["", ".", "", "@", ""],
            [(0, 0), (1, 0), (2, 0), (3, 0)],
            [
                p._first_name(ctx),
                p._last_name(ctx),
                pa.array(p._ints(ctx, "suffix", 1, 4)),
                pa.array(domains),
            ],
            ctx.n_rows,
        )

    def company(ctx, spec):
        tld = ".test" if spec.get("domains") == "realistic" else ".example"
        return p.kernel_ops.template_strings(
            ["", ".", "@", tld],
            [(0, 0), (1, 0), (2, 0)],
            [p._first_name(ctx), p._last_name(ctx), pa.array(["acme"])],
            ctx.n_rows,
        )

    def phone(ctx, spec):
        assignable = spec.get("range") == "assignable"
        return p.kernel_ops.template_strings(
            ["(", ") ", "-", ""],
            [(0, 0), (1, 0), (2, 4)],
            [
                pa.array(p._ints(ctx, "area", 200, 203)),
                pa.array(p._ints(ctx, "exchange", 201, 204)) if assignable else pa.array([555]),
                pa.array(
                    p._ints(ctx, "subscriber", 1000, 1003)
                    if assignable
                    else p._ints(ctx, "subscriber", 100, 103)
                ),
            ],
            ctx.n_rows,
        )

    def uri(ctx, spec):
        host = "host.test" if spec.get("domains") == "realistic" else "example.org"
        return p.kernel_ops.template_strings(
            ["https://", "/", ""],
            [(0, 0), (1, 0)],
            [pa.array([host]), pa.array(["catalog", "home"])],
            ctx.n_rows,
        )

    for name, fn in zip(PROVIDERS, (email, company, phone, uri), strict=True):
        monkeypatch.setattr(p, "_" + name, fn)
    return p


def form(monkeypatch, provider, explicit=False):
    baseline_provider(monkeypatch)
    gen = {"strategy": "faker", "provider": provider}
    if explicit:
        gen["range" if provider == "phone_number" else "domains"] = (
            "assignable" if provider == "phone_number" else "realistic"
        )
    return gen, recorder().capture_identifier_form(gen)


def pool(v, recipe):
    obj = v.Pools.__new__(v.Pools)
    obj.domain_path = "stub"
    obj.identifier_forms = [recipe]
    obj.names = SimpleNamespace()
    obj.native = SimpleNamespace()
    obj.pool_for = lambda *a: None
    obj.effective = lambda t, c, g: g
    return obj


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("explicit", [False, True])
def test_identifier_component_verifier_exit_codes(monkeypatch, tmp_path, provider, explicit):
    gen, recipe = form(monkeypatch, provider, explicit)
    valid = {
        "email": "alice.smith1@" + ("mail.test" if explicit else "example.org"),
        "company_email": "alice.smith@acme" + (".test" if explicit else ".example"),
        "phone_number": "(200) 201-1000" if explicit else "(200) 555-0100",
        "uri": "https://" + ("host.test" if explicit else "example.org") + "/home",
    }[provider]
    invalid = {
        "email": "alice.smith1@gmail.com",
        "company_email": "alice.smith@acme.com",
        "phone_number": "(200) 555-0200",
        "uri": valid.replace("https://", "http://"),
    }[provider]
    v = verifier()
    raw, baseline, _ = sample()
    raw["tables"]["parent"]["columns"]["identifier"] = {"generator": gen, "nullable": True}
    baseline[0]["parent"]["identifier"] = valid
    baseline[1]["parent"]["identifier"] = "string"
    current = copy.deepcopy(baseline)
    monkeypatch.setattr(v, "load_schema_json", lambda *a: raw)
    monkeypatch.setattr(v, "load", lambda *a, **kw: {"identifier_forms": [recipe]})
    monkeypatch.setattr(v, "sha256", lambda *a: "a" * 64)
    monkeypatch.setattr(v, "authenticate_run", lambda *a: None)
    monkeypatch.setattr(
        v, "load_run", lambda tool, *a: current if tool.startswith("shape") else baseline
    )
    monkeypatch.setattr(v, "Pools", lambda *a: pool(v_original, recipe))
    monkeypatch.setattr(v, "manifest", lambda: {"shape_commit": "a" * 40})
    v_original = verifier()
    args = ["--no-generate", "--domain", "stub", "--report", str(tmp_path / "report.json")]
    assert v.main(args) == 0
    print(provider, "explicit" if explicit else "default", "positive exit=0")
    current[0]["parent"]["identifier"] = invalid
    assert v.main(args) == 1
    report = json.loads((tmp_path / "report.json").read_text())
    assert any("(d) parent.identifier: vocab" in x for x in report["flagged"])
    print(provider, "explicit" if explicit else "default", "negative exit=1")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_identifier_form_is_frozen_and_component_boundaries(monkeypatch, provider):
    gen, recipe = form(monkeypatch, provider)
    v = verifier()
    p = pool(v, recipe)
    fn, _ = p.component_fn(gen)
    first = (
        "".join(
            recipe["literals"][i] + str(recipe["components"][slot][0]).zfill(width)
            for i, (slot, width) in enumerate(recipe["slots"])
        )
        + recipe["literals"][-1]
    )
    assert fn(pd.Series([first])) == 1
    for index in range(len(recipe["slots"])):
        broken = (
            "".join(
                recipe["literals"][i]
                + ("invalid" if i == index else str(recipe["components"][slot][0]).zfill(width))
                for i, (slot, width) in enumerate(recipe["slots"])
            )
            + recipe["literals"][-1]
        )
        assert fn(pd.Series([broken])) == 0
    for bad_count, expected in ((1, True), (2, False)):
        a = pd.Series([first] * 1000)
        b = a.copy()
        b.iloc[:bad_count] = "invalid"
        B = v.merge_baselines([v.baseline_distances(a, a)] * 4)
        assert v.compare_column(a, b, B, None, (fn, "frozen"))["checks"]["vocab"] is expected
    changed = {
        **gen,
        "range" if provider == "phone_number" else "domains": "assignable"
        if provider == "phone_number"
        else "realistic",
    }
    with pytest.raises(v.RecordingError, match="identifier"):
        p.component_fn(changed)
    _, replacement = form(monkeypatch, provider, True)
    assert replacement != recipe
    assert pool(v, replacement).component_fn(changed) is not None


def test_capture_uses_baseline_template_and_restores_helpers(monkeypatch):
    p = baseline_provider(monkeypatch)
    original = (p._pick, p._ints, p.kernel_ops.template_strings, p.kernel_ops.pool_take)
    result = recorder().capture_identifier_form({"strategy": "faker", "provider": "phone_number"})
    assert result["components"] == [[200, 201, 202], [555], [100, 101, 102]]
    assert result["slots"] == [[0, 0], [1, 0], [2, 4]]
    assert original == (p._pick, p._ints, p.kernel_ops.template_strings, p.kernel_ops.pool_take)


@pytest.mark.parametrize(
    "damage", ["missing", "not_list", "not_object", "empty_components", "width"]
)
def test_identifier_form_damage_fails_closed(monkeypatch, damage):
    gen, recipe = form(monkeypatch, "email")
    v = verifier()
    p = pool(v, recipe)
    if damage == "missing":
        p.identifier_forms = []
    elif damage == "not_list":
        p.identifier_forms = "invalid"
    elif damage == "not_object":
        p.identifier_forms = ["invalid"]
    elif damage == "empty_components":
        recipe["components"][0] = []
    else:
        recipe["slots"][0][1] = "invalid"
    with pytest.raises(v.RecordingError, match="identifier"):
        p.component_fn(gen)
