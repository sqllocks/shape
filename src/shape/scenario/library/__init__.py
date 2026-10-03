"""The starter scenario library: named, versioned scenarios with answer keys, and named suites.

``list_scenarios`` reads ``index.json``; ``run_scenario`` runs one and compares it with its answer
key; ``run_suite`` runs a suite of them. The data lives next to this file (``scenarios/<id>/`` and
``suites/<name>.json``); ``docs/SCENARIO_LIBRARY.md`` describes every scenario and the formats.
"""

from shape.scenario.library.formats import (
    EXPECT_FORMAT,
    LIBRARY_FORMAT,
    SCENARIO_FORMAT,
    SUITE_FORMAT,
    LibraryError,
    UnknownScenarioError,
)
from shape.scenario.library.run import (
    Mismatch,
    Outcome,
    ScenarioResult,
    list_scenarios,
    load_expect,
    load_scenario,
    mismatches,
    run_scenario,
)
from shape.scenario.library.suite import SuiteResult, list_suites, load_suite, run_suite

__all__ = [
    "EXPECT_FORMAT",
    "LIBRARY_FORMAT",
    "SCENARIO_FORMAT",
    "SUITE_FORMAT",
    "LibraryError",
    "Mismatch",
    "Outcome",
    "ScenarioResult",
    "SuiteResult",
    "UnknownScenarioError",
    "list_scenarios",
    "list_suites",
    "load_expect",
    "load_scenario",
    "load_suite",
    "mismatches",
    "run_scenario",
    "run_suite",
]
