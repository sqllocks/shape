"""The Spark live test removes the Delta tables the worker wrote, in each kind of Lakehouse.

Regression for the leftover ``shape_live_*_shape_live_*_{customer,order}`` tables in the
schema-enabled Lakehouse's ``dbo``: the test never removed them. It now does so in a ``finally``,
through ``remove_spark_tables``, which this offline test checks.
"""

import ast
import importlib.util
import inspect
from pathlib import Path

_path = Path(__file__).with_name("test_live_scale_sinks.py")
_spec = importlib.util.spec_from_file_location("live_scale_sinks_under_test", _path)
live = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(live)


class Storage:
    def __init__(self):
        self.removed = []

    def remove(self, path, *, recursive=False):
        assert recursive
        self.removed.append(path)


def test_schema_enabled_tables_are_removed_from_their_schema_folder():
    storage = Storage()
    got = live.remove_spark_tables(storage, "ws", "lh", "dbo", "p", ["p_customer", "p_order"])
    assert (
        storage.removed
        == got
        == [
            "onelake://ws/lh/Tables/dbo/p_p_customer",
            "onelake://ws/lh/Tables/dbo/p_p_order",
        ]
    )


def test_classic_tables_are_removed_from_tables():
    storage = Storage()
    live.remove_spark_tables(storage, "ws", "lh", None, "p", ["p_customer"])
    assert storage.removed == ["onelake://ws/lh/Tables/p_p_customer"]


def test_the_spark_test_removes_its_tables_in_a_finally():
    tree = ast.parse(inspect.getsource(live.test_fabric_spark_runs_the_worker_notebook_to_success))
    removals = [
        t
        for t in ast.walk(tree)
        if isinstance(t, ast.Try)
        and any(
            isinstance(n, ast.Call) and getattr(n.func, "id", "") == "remove_spark_tables"
            for f in t.finalbody
            for n in ast.walk(f)
        )
    ]
    assert removals, "the teardown must run in a finally, so a failed run also removes its tables"
