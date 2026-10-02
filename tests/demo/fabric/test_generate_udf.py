"""PF-06: the ``generateSample`` Fabric User Data Function, called through the real Fabric SDK.

Needs the SDK (and unixODBC for pyodbc) and an installed domain, but not Delta or Spark, so the
``pure-wheel`` job also runs this file against the pure wheel with ``SHAPE_KERNEL=python``.
Not covered, because it needs a Fabric workspace: the Functions activity, the portal's Test mode and
the real response size limit. Those are the live dry-run checklist in
integrations/fabric/RUNBOOK.md.
"""

from __future__ import annotations

import ast
import importlib.util
import inspect

import pandas as pd
import pytest
from fabric_helpers import UDF_DIR

from shape.integrations.fabric import generation


def _load_function_app():
    spec = importlib.util.spec_from_file_location("function_app_gen", UDF_DIR / "function_app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def app():
    return _load_function_app()


def _raw(app, name: str):
    return inspect.unwrap(getattr(app, name)._function.get_user_function())


def test_generate_sample_is_registered_with_the_planned_signature(app):
    import fabric.functions as fn

    assert type(app.generateSample).__name__ == "FunctionBuilder"
    f = _raw(app, "generateSample")
    names = f.__code__.co_varnames[: f.__code__.co_argcount]
    assert names == ("domain", "table", "rows", "seed")
    assert f.__defaults__ == (10000, 42)
    tree = ast.parse((UDF_DIR / "function_app.py").read_text())
    node = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "generateSample"
    )
    assert ast.unparse(node.returns) == "pd.DataFrame"
    assert [ast.unparse(a.annotation) for a in node.args.args] == ["str", "str", "int", "int"]
    assert not any("lakehouse" in ast.unparse(d) for d in node.decorator_list)
    assert fn.UserDataFunctions  # the SDK is the real one


def test_generate_sample_through_the_function(app):
    frame = _raw(app, "generateSample")("retail", "customer", 120, 5)
    assert isinstance(frame, pd.DataFrame) and len(frame) == 120
    assert list(frame.columns)[:2] == ["customer_id", "first_name"]
    default = _raw(app, "generateSample")("retail", "customer")
    assert len(default) == 10000  # rows defaults to 10000
    assert frame.equals(_raw(app, "generateSample")("retail", "customer", 120, 5))


@pytest.mark.parametrize(
    "args",
    [
        ("../x", "customer"),
        ("retail", "customer; --"),
        ("retail", "nope"),
        ("retail", "customer", 0),
    ],
)
def test_generate_sample_errors_reach_the_caller_as_user_thrown_errors(app, args):
    import fabric.functions as fn

    with pytest.raises(fn.UserThrownError):
        _raw(app, "generateSample")(*args)


def test_the_response_stays_under_the_30_mb_limit(app, monkeypatch):
    monkeypatch.setattr(generation, "MAX_RESPONSE_BYTES", 100_000)
    frame = _raw(app, "generateSample")("retail", "order_line", 20000, 1)
    assert 0 < len(frame) < 20000
    assert len(frame.to_json(orient="split", date_format="iso")) <= 100_000


def test_requirements_note_names_the_domains_wheel():
    text = (UDF_DIR / "requirements.md").read_text(encoding="utf-8")
    assert "sqllocks-shape-domains" in text and "generateSample" in text
