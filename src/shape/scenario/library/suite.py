"""Named suites: a list of library scenarios run together, each compared with its answer key.

A suite is ``{"format": "shape-suite", "version": 1, "scenarios": [...]}``; the built-in ones are
``suites/<name>.json``. ``run_suite`` takes a built-in name or the path of a suite file. An unknown
scenario or a malformed suite is a :class:`~shape.scenario.library.formats.LibraryError` raised
before anything runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.scenario.library import formats
from shape.scenario.library.formats import LibraryError, parse_suite, read_json
from shape.scenario.library.run import ScenarioResult, list_scenarios, run_scenario


@dataclass
class SuiteResult:
    name: str
    scale: str | None
    results: list[ScenarioResult] = field(default_factory=list)

    @property
    def met(self) -> bool:
        return all(r.met for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.name,
            "scale": self.scale,
            "met": self.met,
            "scenarios": [
                {
                    "scenario": r.outcome.scenario,
                    "met": r.met,
                    "mismatches": [
                        {"expected": m.expected, "observed": m.observed} for m in r.mismatches
                    ],
                    "outcome": r.outcome.to_dict(),
                }
                for r in self.results
            ],
        }


def list_suites(root: Path | None = None) -> list[str]:
    return sorted(p.stem for p in ((root or formats.ROOT) / "suites").glob("*.json"))


def load_suite(target: str | Path, root: Path | None = None) -> dict[str, Any]:
    """A built-in suite by name, else the suite file at ``target``."""
    base = root or formats.ROOT
    built_in = base / "suites" / f"{target}.json"
    if isinstance(target, str) and built_in.is_file():
        path = built_in
    elif Path(target).is_file():
        path = Path(target)
    else:
        raise LibraryError(
            f"no suite {str(target)!r}: not a file, and the built-in suites are "
            f"{', '.join(list_suites(base))}"
        )
    doc = parse_suite(read_json(path, "suite"), f"suite {path.name}")
    doc.setdefault("name", path.stem)
    return doc


def run_suite(
    target: str | Path,
    *,
    scale: str | None = None,
    seed: int | None = None,
    output: str | Path | None = None,
    root: Path | None = None,
) -> SuiteResult:
    """Run every scenario of a suite. All names are checked first, so a typo runs nothing."""
    doc = load_suite(target, root)
    known = {e["id"] for e in list_scenarios(root)}
    missing = [s for s in doc["scenarios"] if s not in known]
    if missing:
        raise LibraryError(
            f"suite {doc['name']} names unknown scenarios: {', '.join(missing)}; "
            f"the library has: {', '.join(sorted(known))}"
        )
    result = SuiteResult(str(doc["name"]), scale)
    for name in doc["scenarios"]:
        result.results.append(run_scenario(name, scale=scale, seed=seed, output=output, root=root))
    return result
