"""The Shape pytest plugin (``pytest11`` entry point ``shape``): generated data for tests.

* ``shape_dataset``: a factory fixture, ``shape_dataset("retail", scale="tiny", seed=7,
  tables=["order"])``, that returns the generated tables (Arrow; ``as_pandas()`` with pandas). A
  result is generated once per session for each ``(spec digest, scale, seed)``.
* ``@pytest.mark.shape_scenario("library:NAME", scale=..., seed=...)`` with the ``shape_scenario``
  fixture: runs a library scenario and hands the test its
  :class:`~shape.scenario.library.ScenarioResult`.
* ``--shape-seed N``: overrides every seed, of the fixture and of scenarios.

Nothing is written outside ``tmp_path_factory``. Turn the plugin off with ``-p no:shape``. See
``docs/TESTING_WITH_SHAPE.md``.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from shape.testdata.datasets import DatasetCache, ShapeDataset

LIBRARY_PREFIX = "library:"


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("shape", "Shape test data")
    group.addoption(
        "--shape-seed",
        action="store",
        type=int,
        default=None,
        metavar="N",
        help="use seed N for every Shape dataset and scenario in this run, whatever the tests ask",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        'shape_scenario("library:NAME", scale=None, seed=None): run the library scenario NAME '
        "and pass its result to the test through the shape_scenario fixture",
    )


@pytest.fixture(scope="session")
def _shape_cache() -> DatasetCache:
    return DatasetCache()


@pytest.fixture(scope="session")
def _shape_scenarios() -> dict[tuple[str, str | None, int | None], Any]:
    return {}


@pytest.fixture
def shape_dataset(
    request: pytest.FixtureRequest, _shape_cache: DatasetCache
) -> Callable[..., ShapeDataset]:
    """``shape_dataset(spec, scale="tiny", seed=None, tables=None)``: the generated tables of a
    domain name, schema file or schema, cached for the session by ``(spec digest, scale, seed)``.
    ``--shape-seed`` overrides ``seed``."""
    forced = request.config.getoption("shape_seed")

    def make(
        spec: Any, *, scale: str = "tiny", seed: int | None = None, tables: list[str] | None = None
    ) -> ShapeDataset:
        return _shape_cache.dataset(
            spec, scale=scale, seed=seed, tables=tables, override_seed=forced
        )

    return make


@pytest.fixture
def shape_scenario(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    _shape_scenarios: dict[tuple[str, str | None, int | None], Any],
) -> Any:
    """The :class:`~shape.scenario.library.ScenarioResult` of the library scenario named by the
    test's ``shape_scenario`` marker (cached for the session). Its tables are written under
    ``tmp_path_factory``."""
    from shape.scenario.library import run_scenario

    marker = request.node.get_closest_marker("shape_scenario")
    if marker is None:
        pytest.fail('the shape_scenario fixture needs @pytest.mark.shape_scenario("library:NAME")')
    if len(marker.args) != 1 or not str(marker.args[0]).startswith(LIBRARY_PREFIX):
        pytest.fail(
            f'shape_scenario takes one name of the form "library:NAME", got {marker.args!r}'
        )
    unknown = sorted(set(marker.kwargs) - {"scale", "seed"})
    if unknown:
        pytest.fail(f"shape_scenario does not take {', '.join(unknown)}; it takes scale and seed")
    name = str(marker.args[0])[len(LIBRARY_PREFIX) :]
    forced = request.config.getoption("shape_seed")
    seed = forced if forced is not None else marker.kwargs.get("seed")
    scale = marker.kwargs.get("scale")
    key = (name, scale, seed)
    if key not in _shape_scenarios:
        out = tmp_path_factory.mktemp("shape_scenario")
        _shape_scenarios[key] = run_scenario(name, scale=scale, seed=seed, output=out)
    return _shape_scenarios[key]
