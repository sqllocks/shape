"""W5-05 item 4: the Shape pytest plugin, tested with ``pytester``."""

from __future__ import annotations

import importlib.metadata as md
import subprocess
import sys
import tomllib
from pathlib import Path

import numpy  # noqa: F401  # pytester restores sys.modules after each test: C extensions that a
import pyarrow  # noqa: F401  # test imports for the first time would then load twice, so import
import pytest  # them (and shape's generation) while the tests are collected

import shape.generation.engine  # noqa: F401

pytest_plugins = ["pytester"]
pytest.importorskip("shape_domains")

try:
    import pandas  # noqa: F401
except ImportError:  # the test that needs it says so
    pandas = None

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = "shape.testdata.pytest_plugin"
ON = ["-p", "no:shape", "-p", PLUGIN, "-p", "no:cacheprovider"]  # the plugin, whatever the install


def run(pytester: pytest.Pytester, source: str, *args: str):
    pytester.makepyfile(source)
    return pytester.runpytest_subprocess(*ON, *args)


def test_the_plugin_is_registered_as_a_pytest11_entry_point_behind_the_pytest_extra():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())
    assert project["project"]["entry-points"]["pytest11"] == {"shape": PLUGIN}
    assert project["project"]["optional-dependencies"]["pytest"] == ["pytest>=8.3"]
    installed = {e.name: e.value for e in md.entry_points(group="pytest11")}
    assert installed.get("shape") == PLUGIN


HEAVY = ("pyarrow", "numpy", "pandas", "shape.generation", "shape.scenario")


def test_loading_the_plugin_imports_nothing_heavy():
    """A session that never uses Shape pays only for importing the plugin: the heavy imports
    (Arrow, NumPy, the generation engine) wait for the first fixture or marker use."""
    code = (
        "import sys, shape.testdata.pytest_plugin\n"
        f"print([m for m in {HEAVY!r} if m in sys.modules or any(k.startswith(m + '.') "
        "for k in sys.modules)])"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


def test_a_whole_session_that_does_not_use_the_plugin_imports_nothing_heavy(pytester):
    pytester.makepyfile(
        f"""
        import sys

        def test_it():
            loaded = [m for m in {HEAVY!r} if any(k == m or k.startswith(m + ".") for k in sys.modules)]
            assert loaded == []
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)


def test_the_installed_plugin_loads_on_its_own(pytester):
    pytester.makepyfile(
        """
        def test_it(shape_dataset):
            assert set(shape_dataset("retail", tables=["order"])) == {"order"}
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=1)


def test_nothing_is_active_when_the_plugin_is_not_loaded(pytester):
    pytester.makepyfile(
        """
        def test_it(shape_dataset):
            pass
        """
    )
    result = pytester.runpytest_subprocess("-p", "no:shape", "-p", "no:cacheprovider")
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*fixture 'shape_dataset' not found*"])
    pytester.makepyfile(test_x="def test_it(): pass")
    option = pytester.runpytest_subprocess("-p", "no:shape", "--shape-seed", "3")
    assert option.ret != 0 and "unrecognized arguments: --shape-seed" in option.stderr.str()


def test_shape_dataset_returns_arrow_tables_for_the_asked_tables(pytester):
    result = run(
        pytester,
        """
        import pyarrow as pa

        def test_tables(shape_dataset):
            data = shape_dataset("retail", scale="tiny", seed=7, tables=["order", "customer"])
            assert list(data) == ["order", "customer"] and len(data) == 2
            assert isinstance(data["order"], pa.Table) and data["order"].num_rows == 100
            assert data.seed == 7 and data.scale == "tiny"
            assert "order_id" in data["order"].column_names
            assert set(data.tables) == {"order", "customer"}

        def test_all_tables_by_default_and_a_preset(shape_dataset):
            assert "order_line" in shape_dataset("retail")
            assert shape_dataset("retail", scale="fabric_demo")["order"].num_rows == 1000
        """,
    )
    result.assert_outcomes(passed=2)


def test_bad_tables_scales_and_specs_fail_the_test_with_a_message(pytester):
    result = run(
        pytester,
        """
        import pytest
        from shape.errors import ShapeError

        def test_unknown_table(shape_dataset):
            with pytest.raises(ShapeError, match="no table nope.*order"):
                shape_dataset("retail", tables=["nope"])

        def test_unknown_scale(shape_dataset):
            with pytest.raises(ShapeError, match="unknown scale 'huge'"):
                shape_dataset("retail", scale="huge")

        def test_unknown_domain(shape_dataset):
            with pytest.raises(Exception, match="nodomain"):
                shape_dataset("nodomain")

        def test_not_a_spec(shape_dataset):
            with pytest.raises(ShapeError, match="domain name, a schema file"):
                shape_dataset(42)

        def test_missing_table_key(shape_dataset):
            with pytest.raises(KeyError, match="no table 'x'"):
                shape_dataset("retail", tables=["order"])["x"]
        """,
    )
    result.assert_outcomes(passed=5)


def test_a_dataset_is_cached_for_the_session_by_spec_scale_and_seed(pytester):
    result = run(
        pytester,
        """
        def test_same_key_is_one_generation(shape_dataset, _shape_cache):
            a = shape_dataset("retail", seed=3)
            b = shape_dataset("retail", seed=3, tables=["order"])
            assert a["order"] is b["order"]
            assert _shape_cache.misses == 1 and _shape_cache.hits == 1

        def test_still_cached_in_the_next_test(shape_dataset, _shape_cache):
            c = shape_dataset("retail", seed=3)
            assert _shape_cache.misses == 1 and _shape_cache.hits == 2
            assert c["order"].num_rows == 100

        def test_another_seed_or_scale_is_another_key(shape_dataset, _shape_cache):
            d = shape_dataset("retail", seed=4)
            e = shape_dataset("retail", seed=3, scale="fabric_demo")
            assert _shape_cache.misses == 3
            assert not d["order"].equals(shape_dataset("retail", seed=3)["order"])
            assert e["order"].num_rows == 1000

        def test_the_same_schema_by_file_or_object_is_the_same_key(
            shape_dataset, _shape_cache, tmp_path
        ):
            import json
            from shape.generation.domains import load_domain

            schema = load_domain("retail").schema
            path = tmp_path / "retail.json"
            path.write_text(json.dumps(schema.to_dict()))
            before = _shape_cache.misses
            a = shape_dataset(str(path), seed=3)
            b = shape_dataset(schema, seed=3)
            c = shape_dataset("retail", seed=3)
            assert a.digest == b.digest == c.digest
            assert _shape_cache.misses == before
        """,
    )
    result.assert_outcomes(passed=4)


def test_a_changed_schema_is_a_different_key(pytester):
    result = run(
        pytester,
        """
        import copy

        def test_digest(shape_dataset, _shape_cache):
            from shape.generation.domains import load_domain

            schema = load_domain("retail").schema
            other = copy.deepcopy(schema)
            other.tables["customer"].columns["first_name"].null_rate = 0.5
            a = shape_dataset(schema, seed=1)
            b = shape_dataset(other, seed=1)
            assert a.digest != b.digest and _shape_cache.misses == 2
        """,
    )
    result.assert_outcomes(passed=1)


@pytest.mark.skipif(pandas is None, reason="pandas is not installed (the dev extra has it)")
def test_as_pandas_converts_when_pandas_is_there(pytester):
    result = run(
        pytester,
        """
        def test_frames(shape_dataset):
            data = shape_dataset("retail", tables=["order"])
            frame = data.as_pandas("order")
            assert len(frame) == 100 and "order_id" in frame.columns
            assert set(data.as_pandas()) == {"order"}
        """,
    )
    result.assert_outcomes(passed=1)


def test_as_pandas_says_so_when_pandas_is_missing(pytester):
    result = run(
        pytester,
        """
        import pytest
        from shape.errors import ShapeError

        def test_missing(shape_dataset, monkeypatch):
            data = shape_dataset("retail", tables=["order"])
            monkeypatch.setitem(__import__("sys").modules, "pandas", None)  # import fails
            with pytest.raises(ShapeError, match="needs pandas"):
                data.as_pandas()
            assert data["order"].num_rows == 100  # Arrow still works
        """,
    )
    result.assert_outcomes(passed=1)


def test_the_shape_seed_option_overrides_every_seed(pytester):
    source = """
        def test_plain(shape_dataset):
            assert shape_dataset("retail", seed=7).seed == 7
            assert shape_dataset("retail").seed == 42  # the schema's own

        def test_forced(shape_dataset):
            assert shape_dataset("retail", seed=7).seed == 99
            assert shape_dataset("retail").seed == 99
        """
    pytester.makepyfile(test_seeds=source)
    pytester.runpytest_subprocess(*ON, "-k", "test_plain").assert_outcomes(passed=1)
    forced = pytester.runpytest_subprocess(*ON, "--shape-seed", "99", "-k", "test_forced")
    forced.assert_outcomes(passed=1)
    bad = pytester.runpytest_subprocess(*ON, "--shape-seed", "ninety")
    assert bad.ret != 0 and "invalid int value" in bad.stderr.str()


def test_the_scenario_marker_runs_a_library_scenario_and_hands_over_the_result(pytester):
    result = run(
        pytester,
        """
        import pytest

        @pytest.mark.shape_scenario("library:nulls_injected", scale="tiny")
        def test_nulls(shape_scenario):
            assert shape_scenario.met
            assert shape_scenario.outcome.gates["null_check"] is False
            assert shape_scenario.outcome.scale == "tiny"

        @pytest.mark.shape_scenario("library:schema_rename_column", scale="tiny", seed=3)
        def test_rename(shape_scenario):
            assert shape_scenario.outcome.seed == 3
            changes = shape_scenario.outcome.drift[0]["changes"]
            assert {"column": "order.status", "kind": "column_removed"} in changes

        def test_no_marker(shape_scenario):
            pass
        """,
        "--strict-markers",
    )
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*needs @pytest.mark.shape_scenario*"])


def test_a_malformed_scenario_marker_fails_the_test_with_a_message(pytester):
    result = run(
        pytester,
        """
        import pytest

        @pytest.mark.shape_scenario("nulls_injected")
        def test_no_prefix(shape_scenario): pass

        @pytest.mark.shape_scenario()
        def test_no_name(shape_scenario): pass

        @pytest.mark.shape_scenario("library:clean_baseline", colour="red")
        def test_extra(shape_scenario): pass

        @pytest.mark.shape_scenario("library:ghost")
        def test_unknown(shape_scenario): pass
        """,
    )
    result.assert_outcomes(errors=4)
    result.stdout.fnmatch_lines(
        ["*of the form*library:NAME*", "*does not take colour*", "*unknown scenario 'ghost'*"]
    )


def test_a_scenario_is_run_once_per_session_and_seed_option_overrides_the_marker(pytester):
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.shape_scenario("library:clean_baseline", scale="tiny", seed=1)
        def test_a(shape_scenario, _shape_scenarios):
            assert shape_scenario.outcome.seed == int(__import__("os").environ["EXPECT_SEED"])

        @pytest.mark.shape_scenario("library:clean_baseline", scale="tiny", seed=1)
        def test_b(shape_scenario, _shape_scenarios):
            assert len(_shape_scenarios) == 1
        """
    )
    import os

    os.environ["EXPECT_SEED"] = "1"
    try:
        pytester.runpytest_subprocess(*ON).assert_outcomes(passed=2)
        os.environ["EXPECT_SEED"] = "77"
        pytester.runpytest_subprocess(*ON, "--shape-seed", "77").assert_outcomes(passed=2)
    finally:
        del os.environ["EXPECT_SEED"]


def test_the_plugin_writes_nothing_outside_tmp_path_factory(pytester, tmp_path):
    pytester.makepyfile(
        """
        import pytest

        @pytest.mark.shape_scenario("library:clean_baseline", scale="tiny")
        def test_it(shape_scenario, shape_dataset, tmp_path_factory):
            shape_dataset("retail")
            assert shape_scenario.outcome.files
            for f in shape_scenario.outcome.files:
                assert str(f).startswith(str(tmp_path_factory.getbasetemp()))
        """
    )
    base = tmp_path / "base"
    result = pytester.runpytest_subprocess(*ON, "--basetemp", str(base))
    result.assert_outcomes(passed=1)
    assert list(base.rglob("*.parquet"))  # the scenario's tables went under the base temp dir
    strays = [
        p
        for p in Path(pytester.path).rglob("*")
        if p.is_file()
        and p.suffix != ".py"
        and p.name not in ("stdout", "stderr")
        and "__pycache__" not in p.parts
    ]
    assert strays == []  # nothing in the working directory
