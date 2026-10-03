"""W1-05: one compatibility test per plugin entry-point group.

A change to a Protocol or to a data type a plugin touches must fail *here* first. Two guards:

* the **baseline** (``api_v1_baseline.json``, from ``scripts/plugin_api_compat.py``) records each
  group's Protocol and data types as API 1.0 shipped them; ``compare`` fails on anything that
  would break an existing plugin (removed or retyped members, new required members, new
  parameters or fields without defaults);
* a **frozen reference plugin** per group, written against API 1.0 and never edited to follow
  a change, must still satisfy the live Protocol and pass the live conformance kit.

The meta-tests at the end show that each kind of break is caught.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from collections.abc import Iterable, Iterator, Mapping
from datetime import date
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

from shape.plugins import kit
from shape.plugins.api import v1

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "plugin_api_compat", ROOT / "scripts" / "plugin_api_compat.py"
)
assert _spec is not None and _spec.loader is not None
compat = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(compat)

BASELINE = json.loads((ROOT / "tests" / "plugins" / "api_v1_baseline.json").read_text("utf-8"))
BATCH = pa.record_batch({"id": [1, 2, 3], "name": ["a", "b", "c"]})


# -- frozen reference plugins: written for API 1.0, never edited to follow a change ----------


class RefSource:
    name = "ref-source"
    schemes = ("ref",)

    def can_open(self, uri: str) -> bool:
        return uri.startswith("ref://")

    def schema(self, uri: str, **options: Any) -> pa.Schema:
        return BATCH.schema

    def read(self, uri: str, **options: Any) -> Iterator[pa.RecordBatch]:
        yield BATCH


class RefSink:
    name = "ref-sink"
    schemes = ("ref",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        return sum(b.num_rows for b in batches)


class RefDetector:
    name = "ref-detector"

    def detect(self, values: pa.Array, column: str) -> v1.Detection | None:
        if len(values) and values.null_count < len(values) and values[0].as_py() == "x":
            return v1.Detection("ref", 0.9)
        return None


class RefFitter:
    name = "ref-fitter"
    families = ("constant",)

    def fit(self, sample: pa.Array) -> v1.FitResult | None:
        return v1.FitResult("constant", {"value": 1.0}, 0.1) if len(sample) else None


class RefStrategy:
    name = "ref-strategy"

    def generate(self, spec: Mapping[str, Any], ctx: v1.GenerationContext) -> pa.Array:
        return pa.array([ctx.row_start + i for i in range(ctx.n_rows)], type=pa.int64())


class RefDistribution:
    name = "ref-distribution"

    def sample(self, params: Mapping[str, float], ctx: v1.GenerationContext) -> pa.Array:
        return pa.array([float(params.get("value", 1.0))] * ctx.n_rows, type=pa.float64())


class RefCalendar:
    name = "ref-calendar"

    def lift(self, start: date, end: date) -> pa.Array:
        return pa.array([1.0] * ((end - start).days + 1), type=pa.float64())


class RefDomain:
    name = "ref-domain"

    def definition(self) -> v1.DomainDefinition:
        return v1.DomainDefinition(schema={"tables": {"t": {"columns": {"id": "int"}}}})


class RefChaos:
    name = "ref-chaos"

    def mutate(self, batch: pa.RecordBatch, seed: int) -> tuple[pa.RecordBatch, v1.ChaosReport]:
        return batch, v1.ChaosReport("ref-chaos", 0)


class RefEmitter:
    name = "ref-emitter"
    schemes = ("ref",)

    def emit(self, uri: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        return sum(b.num_rows for b in batches)


class RefStreamSource:
    name = "ref-stream"
    schemes = ("ref",)

    def read(
        self, uri: str, start: v1.StreamOffset | None = None, **options: Any
    ) -> Iterator[tuple[v1.StreamOffset, pa.RecordBatch]]:
        first = 0 if start is None else int(start.value["n"])
        for n in range(first, 3):
            yield v1.StreamOffset({"n": n + 1}), BATCH


class RefTransform:
    name = "ref-transform"

    def apply(self, tables: Mapping[str, pa.Table], **options: Any) -> dict[str, pa.Table]:
        return dict(tables)


class RefCommand:
    name = "ref-command"
    help = "a reference command"

    def configure(self, parser: Any) -> None:
        parser.add_argument("--flag", action="store_true")

    def run(self, args: Any) -> int:
        return 0


class RefReport:
    name = "ref-report"
    extension = ".txt"

    def render(self, report: Mapping[str, Any]) -> bytes:
        return b"report"


# group -> (reference plugin, the sample its kit check takes)
REFERENCE: dict[str, tuple[Any, dict[str, Any]]] = {
    "shape.sources": (RefSource(), {"uri": "ref://x"}),
    "shape.sinks": (RefSink(), {"uri": "ref://x", "batches": [BATCH]}),
    "shape.detectors": (RefDetector(), {"positives": [["x", "y"]], "negatives": [["q"]]}),
    "shape.fitters": (RefFitter(), {"sample": [1.0, 1.0, 1.0]}),
    "shape.strategies": (RefStrategy(), {"spec": {}, "layout_independent": True}),
    "shape.distributions": (RefDistribution(), {"params": {"value": 2.0}}),
    "shape.calendars": (RefCalendar(), {}),
    "shape.domains": (RefDomain(), {}),
    "shape.chaos": (RefChaos(), {"batch": BATCH}),
    "shape.emitters": (RefEmitter(), {"uri": "ref://x", "batches": [BATCH]}),
    "shape.stream_sources": (RefStreamSource(), {"uri": "ref://x"}),
    "shape.transforms": (RefTransform(), {"tables": {"t": pa.table({"a": [1]})}}),
    "shape.commands": (RefCommand(), {"argv": ["--flag"]}),
    "shape.reports": (RefReport(), {"report": {"k": 1}}),
}

GROUPS = sorted(v1.GROUPS)


# -- one compatibility test per group --------------------------------------------------------


@pytest.mark.parametrize("group", GROUPS)
def test_group_protocol_is_compatible_with_the_baseline(group: str) -> None:
    """The Protocol and data types of ``group`` break no plugin written for API 1.0."""
    problems = compat.compare(BASELINE, compat.snapshot(), group=group)
    assert not problems, (
        "plugin API v1 would break existing plugins:\n  - "
        + "\n  - ".join(problems)
        + "\nA breaking change needs a new API major version (docs/plugins/stability.md). "
        "Additive changes: run `python scripts/plugin_api_compat.py --write`."
    )


@pytest.mark.parametrize("group", GROUPS)
def test_frozen_reference_plugin_still_conforms(group: str) -> None:
    """A plugin written against API 1.0 still satisfies the live Protocol and the live kit."""
    obj, sample = REFERENCE[group]
    assert isinstance(obj, v1.PROTOCOLS[v1.GROUPS[group]])
    kit.check_plugin(group, obj, **sample)
    kit.CHECKS[group](obj, **sample)


# -- the guards cover every group, now and for groups added later ---------------------------


def test_every_group_has_a_baseline_entry_and_a_reference_plugin() -> None:
    assert set(v1.GROUPS) == set(BASELINE["groups"]) == set(REFERENCE) == set(compat.GROUP_TYPES)


def test_every_data_type_is_guarded_by_some_group() -> None:
    import dataclasses

    declared = {n for n in v1.__all__ if dataclasses.is_dataclass(getattr(v1, n))}
    guarded = {t for types in compat.GROUP_TYPES.values() for t in types}
    assert declared == guarded, f"unguarded data types: {sorted(declared - guarded)}"


def test_baseline_api_major_is_current() -> None:
    assert BASELINE["api"].split(".")[0] == v1.SHAPE_API.split(".")[0]


def test_script_check_passes() -> None:
    assert compat.main(["--check"]) == 0


# -- the guards fail when they should (each kind of break) -----------------------------------


def _mutated(edit: Any) -> dict[str, Any]:
    live = copy.deepcopy(compat.snapshot())
    edit(live["groups"])
    return live


def _problems(edit: Any, group: str = "shape.detectors") -> list[str]:
    return compat.compare(BASELINE, _mutated(edit), group=group)


def test_removed_method_is_breaking() -> None:
    assert _problems(lambda g: g["shape.detectors"]["protocol_shape"]["methods"].pop("detect"))


def test_new_required_method_is_breaking() -> None:
    def edit(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["methods"]["extra"] = {"params": [], "returns": ""}

    assert any("new required method" in p for p in _problems(edit))


def test_new_required_attribute_is_breaking() -> None:
    def edit(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["attributes"]["version"] = "str"

    assert any("new required attribute" in p for p in _problems(edit))


def test_removed_or_retyped_attribute_is_breaking() -> None:
    def removed(g: dict[str, Any]) -> None:
        del g["shape.detectors"]["protocol_shape"]["attributes"]["name"]

    def retyped(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["attributes"]["name"] = "int"

    assert _problems(removed) and _problems(retyped)


def test_changed_return_type_is_breaking() -> None:
    def edit(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["methods"]["detect"]["returns"] = "str"

    assert any("return type" in p for p in _problems(edit))


def test_renamed_reordered_or_removed_parameter_is_breaking() -> None:
    def renamed(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["methods"]["detect"]["params"][1]["name"] = "col"

    def removed(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["methods"]["detect"]["params"].pop()

    def reordered(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["methods"]["detect"]["params"].reverse()

    assert _problems(renamed) and _problems(removed) and _problems(reordered)


def test_new_parameter_needs_a_default() -> None:
    def bare(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["methods"]["detect"]["params"].append(
            {"name": "x", "kind": "POSITIONAL_OR_KEYWORD", "annotation": "int", "default": None}
        )

    def defaulted(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol_shape"]["methods"]["detect"]["params"].append(
            {"name": "x", "kind": "POSITIONAL_OR_KEYWORD", "annotation": "int", "default": "0"}
        )

    assert any("no default" in p for p in _problems(bare))
    assert _problems(defaulted) == []  # additive: allowed


def test_data_type_field_rules() -> None:
    def removed(g: dict[str, Any]) -> None:
        g["shape.detectors"]["types"]["Detection"]["fields"].pop()

    def retyped(g: dict[str, Any]) -> None:
        g["shape.detectors"]["types"]["Detection"]["fields"][0]["type"] = "int"

    def bare(g: dict[str, Any]) -> None:
        g["shape.detectors"]["types"]["Detection"]["fields"].append(
            {"name": "extra", "type": "int", "has_default": False}
        )

    def defaulted(g: dict[str, Any]) -> None:
        g["shape.detectors"]["types"]["Detection"]["fields"].append(
            {"name": "extra", "type": "int", "has_default": True}
        )

    def thawed(g: dict[str, Any]) -> None:
        g["shape.detectors"]["types"]["Detection"]["frozen"] = False

    assert _problems(removed) and _problems(retyped) and _problems(bare) and _problems(thawed)
    assert _problems(defaulted) == []  # additive: allowed


def test_removed_group_or_renamed_protocol_is_breaking() -> None:
    assert _problems(lambda g: g.pop("shape.detectors"))

    def renamed(g: dict[str, Any]) -> None:
        g["shape.detectors"]["protocol"] = "Detector"

    assert _problems(renamed)


def test_api_major_change_is_breaking() -> None:
    live = copy.deepcopy(compat.snapshot())
    live["api"] = "2.0"
    assert any("major" in p for p in compat.compare(BASELINE, live))


def test_a_group_added_later_is_not_breaking() -> None:
    live = copy.deepcopy(compat.snapshot())
    live["groups"]["shape.behaviors"] = copy.deepcopy(live["groups"]["shape.transforms"])
    assert compat.compare(BASELINE, live) == []


def test_live_protocol_edit_is_caught_end_to_end(monkeypatch: pytest.MonkeyPatch) -> None:
    """Edit the real Protocol the way a careless change would, and see the comparison fail."""

    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class SemanticDetector(Protocol):
        name: str

        def detect(self, values: pa.Array, column: str, extra: int) -> v1.Detection | None: ...

    monkeypatch.setitem(v1.PROTOCOLS, "SemanticDetector", SemanticDetector)
    problems = compat.compare(BASELINE, compat.snapshot(), group="shape.detectors")
    assert any("detect" in p and "no default" in p for p in problems)


def test_a_reference_plugin_fails_when_the_protocol_gains_a_member(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typing import Protocol, runtime_checkable

    @runtime_checkable
    class SemanticDetector(Protocol):
        name: str

        def detect(self, values: pa.Array, column: str) -> v1.Detection | None: ...

        def explain(self) -> str: ...

    monkeypatch.setitem(v1.PROTOCOLS, "SemanticDetector", SemanticDetector)
    with pytest.raises(kit.ConformanceError, match="does not implement"):
        kit.check_plugin("shape.detectors", RefDetector(), **REFERENCE["shape.detectors"][1])
