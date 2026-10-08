"""Data detective packs: a case with planted problems, a brief and hints, and an answer check.

A pack (``src/shape/scenario/library/detective/NAME.json``, format ``shape-detective-pack``) names
a library scenario that plants the problems, a seed, a brief, hints and the ``findings``: which
table and column hold which failure mode of the catalog. The data is never shipped: ``start``
generates it from the scenario and the seed, so a case can be played again with the same data.

``start`` writes the case to a folder (the data, the brief and the profile of the clean batch) and
never the findings. ``check`` compares an answer (format ``shape-detective-answer``) with the
findings: every planted finding named and none that is not planted is a pass. See
``docs/DETECTIVE.md``. Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.scenario.library import catalog, formats
from shape.scenario.library.formats import LibraryError, check_header, list_text, read_json

PACK_FORMAT = "shape-detective-pack"
ANSWER_FORMAT = "shape-detective-answer"
LEVELS = ("beginner", "intermediate", "advanced")
_NAME = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_PACK_KEYS = {
    "format",
    "version",
    "name",
    "level",
    "brief",
    "hints",
    "scenario",
    "seed",
    "findings",
}


class UnknownPackError(LibraryError):
    """A detective pack name that is not in the library."""


class AnswerError(LibraryError):
    """An answer file that is malformed, of another format, or names a failure mode that does not
    exist."""


def packs_dir(root: Path | None = None) -> Path:
    return (root or formats.ROOT) / "detective"


def _finding(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"table", "column", "mode"}:
        raise LibraryError(f"{what}: a finding has 'table', 'column' and 'mode'")
    table, column, mode = value["table"], value["column"], value["mode"]
    if not isinstance(table, str) or not table:
        raise LibraryError(f"{what}: a finding needs a 'table'")
    if column is not None and (not isinstance(column, str) or not column):
        raise LibraryError(f"{what}: 'column' is a column name, or null for a whole table")
    if not isinstance(mode, str) or not mode:
        raise LibraryError(f"{what}: a finding needs a 'mode' (a failure mode id)")
    return {"table": table, "column": column, "mode": mode}


def parse_pack(doc: Any, what: str) -> dict[str, Any]:
    """The detective pack ``doc``. Raises :class:`LibraryError` for another format, a newer or
    missing version, unknown or missing keys, a level that is not in :data:`LEVELS`, text that is
    empty, a scenario that is not written ``library:NAME``, and findings that are malformed or
    repeat."""
    out = check_header(doc, PACK_FORMAT, what, _PACK_KEYS)
    missing = sorted(_PACK_KEYS - set(out))
    if missing:
        raise LibraryError(f"{what} lacks {', '.join(missing)}")
    if not isinstance(out["name"], str) or not _NAME.match(out["name"]):
        raise LibraryError(f"{what}: 'name' must be a slug such as first-case")
    if out["level"] not in LEVELS:
        raise LibraryError(f"{what}: 'level' must be one of {', '.join(LEVELS)}")
    if not isinstance(out["brief"], str) or not out["brief"].strip():
        raise LibraryError(f"{what}: 'brief' must be text")
    if not list_text(out["hints"]):
        raise LibraryError(f"{what}: 'hints' must be a non-empty list of text")
    scenario = out["scenario"]
    if not isinstance(scenario, str) or not scenario.startswith("library:"):
        raise LibraryError(f"{what}: 'scenario' must be library:NAME")
    if isinstance(out["seed"], bool) or not isinstance(out["seed"], int):
        raise LibraryError(f"{what}: 'seed' must be an integer")
    findings = out["findings"]
    if not isinstance(findings, list) or not findings:
        raise LibraryError(f"{what}: 'findings' must be a non-empty list")
    out["findings"] = [_finding(f, what) for f in findings]
    keys = [tuple(f.values()) for f in out["findings"]]
    if len(set(keys)) != len(keys):
        raise LibraryError(f"{what}: 'findings' names a finding twice")
    return out


def list_packs(root: Path | None = None) -> list[dict[str, Any]]:
    """Every pack of the library, in name order."""
    files = sorted(packs_dir(root).glob("*.json"))
    return [_load(path, root) for path in files]


def _load(path: Path, root: Path | None) -> dict[str, Any]:
    from shape.scenario.library.run import list_scenarios, load_scenario

    pack = parse_pack(read_json(path, "detective pack"), f"pack {path.name}")
    if pack["name"] != path.stem:
        raise LibraryError(f"{path} is the pack {pack['name']!r}, not {path.stem!r}")
    scenario = str(pack["scenario"]).partition(":")[2]
    if scenario not in {e["id"] for e in list_scenarios(root)}:
        raise LibraryError(
            f"pack {pack['name']!r} names the scenario {scenario!r}: not in the library"
        )
    if load_scenario(scenario, root).get("drift"):
        raise LibraryError(
            f"pack {pack['name']!r}: its scenario plants defects in one batch; a drift scenario "
            f"has no single batch to hand out"
        )
    modes = {m["id"] for m in catalog.load_catalog(root)}
    for f in pack["findings"]:
        if f["mode"] not in modes:
            raise LibraryError(
                f"pack {pack['name']!r}: finding mode {f['mode']!r} is not a failure mode of the "
                f"catalog"
            )
    return pack


def load_pack(name: str, root: Path | None = None) -> dict[str, Any]:
    path = packs_dir(root) / f"{name}.json"
    if not _NAME.match(name) or not path.is_file():
        known = [p.stem for p in sorted(packs_dir(root).glob("*.json"))]
        raise UnknownPackError(
            f"unknown detective pack {name!r}; the packs are: {', '.join(known)}"
        )
    return _load(path, root)


def scenario_of(pack: dict[str, Any]) -> str:
    return str(pack["scenario"]).partition(":")[2]


# ---- start ---------------------------------------------------------------------------------------


@dataclass
class Case:
    """What ``start`` wrote."""

    pack: str
    directory: str
    data: list[str] = field(default_factory=list)
    brief: str = ""
    baseline: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "directory": self.directory,
            "data": self.data,
            "brief": self.brief,
            "baseline": self.baseline,
        }


def _brief(pack: dict[str, Any], directory: Path) -> str:
    count = len(pack["findings"])
    planted = f"{count} problems were" if count != 1 else "1 problem was"
    them = "them" if count != 1 else "it"
    return f"""# Case: {pack["name"]} ({pack["level"]})

{pack["brief"]}

Something is wrong with this batch: {planted} planted. Find {them} and say which table and
column hold which failure mode.

## What you have

- `data/`: the batch, one Parquet file per table.
- `baseline.shape`: the profile of the clean batch this one should look like.

## How to look

```
shape profile {directory}/data --dataset --joint -o today.shape --capture full
shape diff {directory}/baseline.shape today.shape
```

`shape check` tells you which rules of a contract the batch breaks; write the contract from what
the baseline profile says is normal (`shape check --help`, `docs/CLI.md`).

## Say what you found

Write an answer file (`docs/DETECTIVE.md` shows its format): for each problem, the table, the
column and the failure mode id. `shape failure-modes list` has the ids. For a column that was
renamed, name the old column. Then ask for a hint, or check your answer:

```
shape detective hint {pack["name"]} 1
shape detective check {pack["name"]} --answer answer.json
```
"""


def start(name: str, directory: str | Path, root: Path | None = None) -> Case:
    """Generate the case of pack ``name`` into ``directory``: ``data/*.parquet``, ``brief.md`` and
    ``baseline.shape``. The folder must not exist or must be empty. Nothing of the findings is
    written."""
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    import shape
    from shape.profile.reference.profile import save
    from shape.scenario.library import run
    from shape.scenario.library.defects import apply_defects

    pack = load_pack(name, root)
    out = Path(directory)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise LibraryError(f"{out} exists and is not an empty folder: start a case in a new folder")
    scenario = run.load_scenario(scenario_of(pack), root)
    schema = run._schema(scenario["domain"])
    seed = int(pack["seed"])
    scale = str(scenario.get("scale", "small"))
    clean = run._generate(schema, scale, seed)
    tables, _ = apply_defects(clean.tables, list(scenario.get("defects", [])), schema, seed)
    data = out / "data"
    data.mkdir(parents=True, exist_ok=True)
    case = Case(pack["name"], str(out))
    for table, arrow in tables.items():
        path = data / f"{table}.parquet"
        pq.write_table(arrow, path)
        case.data.append(str(path))
    with tempfile.TemporaryDirectory() as scratch:
        sources = {}
        for table, arrow in clean.tables.items():
            path = Path(scratch) / f"{table}.parquet"
            pq.write_table(arrow, path)
            sources[table] = str(path)
        baseline = shape.profile(sources, name=f"{pack['name']}-baseline", joint=True)
        case.baseline = str(out / "baseline.shape")
        save(baseline, case.baseline, capture="full")
    case.brief = str(out / "brief.md")
    Path(case.brief).write_text(_brief(pack, out), encoding="utf-8", newline="\n")
    return case


def hint(name: str, n: int, root: Path | None = None) -> str:
    """Hint number ``n`` (counting from 1) of pack ``name``."""
    pack = load_pack(name, root)
    hints = pack["hints"]
    if isinstance(n, bool) or not 1 <= n <= len(hints):
        raise LibraryError(f"pack {name!r} has {len(hints)} hints: ask for 1 to {len(hints)}")
    return str(hints[n - 1])


# ---- the answer ----------------------------------------------------------------------------------


def parse_answer(doc: Any, what: str = "the answer") -> list[dict[str, Any]]:
    """The findings of an answer document. Raises :class:`AnswerError` for another format, a
    missing or newer version, unknown keys, malformed findings and failure modes that are not in
    the catalog."""
    try:
        out = check_header(doc, ANSWER_FORMAT, what, {"format", "version", "findings"})
        findings = out.get("findings")
        if not isinstance(findings, list):
            raise LibraryError(f"{what}: 'findings' must be a list")
        parsed = [_finding(f, what) for f in findings]
    except AnswerError:
        raise
    except LibraryError as exc:
        raise AnswerError(str(exc)) from None
    modes = {m["id"] for m in catalog.load_catalog()}
    for f in parsed:
        if f["mode"] not in modes:
            raise AnswerError(
                f"{what}: {f['mode']!r} is not a failure mode; `shape failure-modes list` has "
                f"them: {', '.join(sorted(modes))}"
            )
    return parsed


def load_answer(path: str | Path) -> list[dict[str, Any]]:
    try:
        doc = read_json(Path(path), "the answer")
    except LibraryError as exc:
        raise AnswerError(str(exc)) from None
    return parse_answer(doc, f"answer {Path(path).name}")


@dataclass
class Verdict:
    """An answer compared with the findings: ``found`` (named and planted), ``missed`` (planted
    and not named) and ``wrong`` (named and not planted)."""

    pack: str
    found: list[dict[str, Any]] = field(default_factory=list)
    missed: list[dict[str, Any]] = field(default_factory=list)
    wrong: list[dict[str, Any]] = field(default_factory=list)

    @property
    def solved(self) -> bool:
        return not self.missed and not self.wrong

    def to_dict(self) -> dict[str, Any]:
        return {
            "pack": self.pack,
            "solved": self.solved,
            "found": self.found,
            "missed": self.missed,
            "wrong": self.wrong,
        }


def _key(finding: dict[str, Any]) -> tuple[str, str | None, str]:
    return (finding["table"], finding["column"], finding["mode"])


def check_answer(pack: dict[str, Any], answer: list[dict[str, Any]]) -> Verdict:
    """Compare ``answer`` (a list of findings) with the findings of ``pack``."""
    planted = {_key(f): f for f in pack["findings"]}
    named = {_key(f): f for f in answer}
    verdict = Verdict(str(pack["name"]))
    verdict.found = [planted[k] for k in planted if k in named]
    verdict.missed = [planted[k] for k in planted if k not in named]
    verdict.wrong = [named[k] for k in named if k not in planted]
    return verdict
