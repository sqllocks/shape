"""W1-15 (#92) deliverables 1, 2 and 4: generator versions, pinning in the spec, and the errors.

A test-only strategy with two versions (``versioning_fixtures.TwoVersions``) stands in for a
strategy whose algorithm changed.
"""

from __future__ import annotations

import json
import warnings
from typing import Any

import pytest
from versioning_fixtures import PROBE, probes, schema, spec_doc

from shape import schemacheck
from shape.builtins.catalog import BUILTINS
from shape.generation import versions
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema, GenSchemaError
from shape.generation.spec_edit import SpecDocument
from shape.generation.spec_schema import published_schema
from shape.generation.versions import GeneratorPinError, GeneratorPinWarning
from shape.plugins.host import default_host
from shape.repro import dataset_id


@pytest.fixture(autouse=True)
def _probes() -> Any:
    with probes():
        yield


def ids(sc: GenSchema, **kw: Any) -> str:
    return dataset_id(Engine(sc, **kw).generate().tables)


def column(sc: GenSchema, **kw: Any) -> list[int]:
    return Engine(sc, **kw).generate().tables["t"].column("x").to_pylist()


# ---- 1. every built-in declares a generator_version -----------------------------------------


def builtin_objects() -> list[tuple[str, str, Any]]:
    host = default_host()
    return [
        (g, n, host.get(g, n))
        for g, n, _ in BUILTINS
        if g in ("shape.strategies", "shape.distributions")
    ]


# Built-ins whose output changed after W1-15, so their version was raised (the older versions
# stay selectable): W8-06 keeps the faker package's identifier providers reserved by default.
RAISED = {("shape.strategies", "faker"): 2}


def test_every_builtin_strategy_and_distribution_declares_version_1() -> None:
    found = builtin_objects()
    assert len([1 for g, _, _ in found if g == "shape.strategies"]) >= 30
    assert len([1 for g, _, _ in found if g == "shape.distributions"]) >= 15
    assert set(RAISED) <= {(g, n) for g, n, _ in found}
    for group, name, obj in found:
        want = RAISED.get((group, name), 1)
        assert getattr(obj, "generator_version", None) == want, (
            f"{group}:{name} must declare generator_version = {want}"
        )
        assert versions.generator_version(obj) == want
        if want > 1:
            assert versions.supported(obj) == (1, want)  # every older version still runs


def test_every_distribution_family_declares_a_version() -> None:
    from shape.builtins.distributions.families import FAMILIES

    assert FAMILIES
    for name, family in FAMILIES.items():
        assert family.generator_version == 1, name


def test_a_name_that_is_a_strategy_and_a_distribution_has_one_version_in_both() -> None:
    host = default_host()
    shared = set(host.names("shape.strategies")) & set(host.names("shape.distributions"))
    assert {"uniform", "normal"} <= shared
    for name in shared:
        a = host.get("shape.strategies", name)
        b = host.get("shape.distributions", name)
        assert versions.generator_version(a) == versions.generator_version(b), name


def test_a_plugin_without_the_attribute_is_version_1() -> None:
    plugin = default_host().get("shape.strategies", "noversion")
    assert not hasattr(plugin, "generator_version")
    assert versions.generator_version(plugin) == 1
    assert versions.supported(plugin) == (1, 1)


@pytest.mark.parametrize("bad", [0, -1, "2", 1.5, True, None])
def test_a_bad_generator_version_is_refused(bad: Any) -> None:
    class Odd:
        generator_version = bad

    with pytest.raises(ValueError, match="generator_version"):
        versions.generator_version(Odd())


def test_the_range_of_a_selectable_and_a_fixed_implementation() -> None:
    host = default_host()
    assert versions.supported(host.get("shape.strategies", PROBE)) == (1, 2)
    assert versions.supported(host.get("shape.strategies", "v2only")) == (2, 2)
    assert versions.supported(host.get("shape.strategies", "weighted_enum")) == (1, 1)


def test_the_conformance_kit_accepts_a_declared_version_and_refuses_a_bad_one() -> None:
    from shape.plugins.kit import ConformanceError, check_strategy

    spec = {"strategy": "v2only"}
    good = default_host().get("shape.strategies", "v2only")
    check_strategy(good, spec)

    class Bad(type(good)):  # type: ignore[misc]
        generator_version = 0

    with pytest.raises(ConformanceError, match="generator_version"):
        check_strategy(Bad(), spec)


# ---- 2. pinning in the spec -------------------------------------------------------------------


def test_the_spec_accepts_a_generators_map_in_both_schemas() -> None:
    doc = spec_doc(generators={PROBE: 1, "sequence": 1})
    assert schemacheck.validate(doc, published_schema()) == []
    sc = GenSchema.from_dict(doc)
    assert sc.generators == {PROBE: 1, "sequence": 1}
    assert GenSchema.from_dict(sc.to_dict()).generators == sc.generators


def test_an_unpinned_schema_does_not_gain_a_generators_key() -> None:
    assert "generators" not in schema().to_dict()


@pytest.mark.parametrize(
    "bad",
    [{PROBE: 0}, {PROBE: -3}, {PROBE: "1"}, {PROBE: 1.5}, {PROBE: True}, {PROBE: None}, [PROBE], 3],
)
def test_a_malformed_generators_map_is_refused_by_both_schemas(bad: Any) -> None:
    doc = spec_doc(generators=bad)
    assert schemacheck.validate(doc, published_schema()) != []
    with pytest.raises(GenSchemaError):
        GenSchema.from_dict(doc)


def test_spec_edit_keeps_the_map_and_edits_it() -> None:
    text = json.dumps(spec_doc(generators={PROBE: 1}, **{"x-note": "keep"}), indent=2) + "\n"
    doc = SpecDocument.loads(text)
    assert doc.dumps() == text  # unchanged: byte for byte
    assert doc.generators == {PROBE: 1}
    doc.set_generator_version("sequence", 1)
    out = json.loads(doc.dumps())
    assert out["generators"] == {PROBE: 1, "sequence": 1} and out["x-note"] == "keep"
    doc.remove_generator_version(PROBE)
    doc.remove_generator_version("sequence")
    assert "generators" not in json.loads(doc.dumps())
    with pytest.raises(KeyError):
        doc.remove_generator_version("sequence")


@pytest.mark.parametrize("bad", [0, -1, True, "1", 1.0])
def test_spec_edit_refuses_a_bad_version(bad: Any) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        SpecDocument.from_dict(spec_doc()).set_generator_version(PROBE, bad)


def test_spec_edit_validate_reports_a_pin_with_its_pointer_and_line() -> None:
    text = json.dumps(spec_doc(generators={PROBE: 3}), indent=2)
    problems = [p for p in SpecDocument.loads(text).validate() if p.level == "error"]
    assert [p.pointer for p in problems] == [f"/generators/{PROBE}"]
    assert problems[0].line is not None
    assert "this Shape has versions 1 to 2 (upgrade Shape)" in problems[0].message


def test_an_unpinned_spec_runs_the_latest_version_and_a_pinned_one_its_pin() -> None:
    unpinned = column(schema())
    assert unpinned == [i * 10 for i in range(20)]  # version 2
    pinned_1 = column(schema(generators={PROBE: 1}))
    assert pinned_1 == list(range(20))  # version 1
    assert column(schema(generators={PROBE: 2})) == unpinned


def test_names_that_are_not_pinned_run_at_the_latest_version() -> None:
    sc = schema(generators={"sequence": 1})  # the probe is not pinned
    assert column(sc) == [i * 10 for i in range(20)]


def test_the_dataset_id_follows_the_pinned_version() -> None:
    v2 = ids(schema())
    v1 = ids(schema(generators={PROBE: 1}))
    assert v1 != v2
    assert v2 == ids(schema(generators={PROBE: 2}))
    assert v1 == ids(schema(generators={PROBE: 1}))  # and is stable


def test_the_engine_argument_overrides_the_specs_pins() -> None:
    sc = schema(generators={PROBE: 2})
    assert column(sc, generators={PROBE: 1}) == list(range(20))
    assert column(schema(generators={PROBE: 1}), generators={PROBE: 2}) == [
        i * 10 for i in range(20)
    ]


def test_the_versions_of_a_run_are_the_pin_or_the_latest() -> None:
    assert Engine(schema()).generator_versions == {PROBE: 2, "sequence": 1}
    assert Engine(schema(generators={PROBE: 1})).generator_versions == {PROBE: 1, "sequence": 1}


def test_a_distribution_the_spec_names_is_counted() -> None:
    doc = spec_doc("distribution")
    doc["tables"]["t"]["columns"]["x"]["generator"].update(
        {"distribution": "normal", "mean": 0, "std_dev": 1}
    )
    got = Engine(GenSchema.from_dict(doc)).generator_versions
    assert got == {"distribution": 1, "normal": 1, "sequence": 1}
    doc["tables"]["t"]["columns"]["x"]["generator"].pop("distribution")
    assert "uniform" in Engine(GenSchema.from_dict(doc)).generator_versions  # the default family


def test_a_pinned_distribution_family_runs_the_pinned_version(monkeypatch: Any) -> None:
    import numpy as np

    from shape.builtins.distributions import families

    class Versioned(families.Normal):
        name = "normal"
        generator_version = 2

        def sample_versioned(
            self, stream: Any, row_start: int, n: int, params: Any, version: int
        ) -> Any:
            base = super().sample(stream, row_start, n, params)
            return base + (1000.0 if version == 2 else 0.0)

    monkeypatch.setitem(families.FAMILIES, "normal", Versioned())
    doc = spec_doc("distribution")
    doc["tables"]["t"]["columns"]["x"]["type"] = "decimal"
    doc["tables"]["t"]["columns"]["x"]["generator"].update(
        {"distribution": "normal", "mean": 0, "std_dev": 1}
    )
    latest = np.asarray(column(GenSchema.from_dict(doc)))
    doc["generators"] = {"normal": 1}
    one = np.asarray(column(GenSchema.from_dict(doc)))
    assert (latest - one == 1000.0).all()
    doc["generators"] = {"normal": 2}
    assert (np.asarray(column(GenSchema.from_dict(doc))) == latest).all()


# ---- 4. errors ----------------------------------------------------------------------------------


def test_a_pin_to_a_version_this_shape_does_not_have_is_refused_with_the_documented_message() -> (
    None
):
    with pytest.raises(GeneratorPinError) as err:
        Engine(schema(generators={PROBE: 3}))
    assert str(err.value) == (
        f"the spec pins {PROBE} at generator version 3; this Shape has versions 1 to 2 "
        "(upgrade Shape)"
    )
    assert (err.value.name, err.value.version, err.value.low, err.value.high) == (PROBE, 3, 1, 2)
    assert isinstance(err.value, ValueError)


def test_the_source_is_named_in_the_message() -> None:
    with pytest.raises(GeneratorPinError, match=r"^SPEC pins v2probe at generator version 3;"):
        versions.check_pins({PROBE: 3}, versions.usage_of(schema().tables), "SPEC")


@pytest.mark.parametrize("version,ok", [(1, True), (2, True), (3, False)])
def test_the_boundaries_of_the_range(version: int, ok: bool) -> None:
    sc = schema(generators={PROBE: version})
    if ok:
        Engine(sc)
    else:
        with pytest.raises(GeneratorPinError):
            Engine(sc)


def test_an_implementation_without_a_selector_has_only_the_version_it_declares() -> None:
    with pytest.raises(GeneratorPinError, match="this Shape has version 2 \\(re-pin the spec\\)"):
        Engine(schema("v2only", generators={"v2only": 1}))
    Engine(schema("v2only", generators={"v2only": 2}))


def test_a_builtin_cannot_be_pinned_beyond_version_1() -> None:
    with pytest.raises(GeneratorPinError, match="sequence at generator version 2"):
        Engine(schema(generators={"sequence": 2}))


def test_a_pin_to_a_name_the_spec_does_not_use_is_a_warning_not_an_error() -> None:
    sc = schema(generators={PROBE: 2, "zipf": 1, "no_such_generator": 9})
    with pytest.warns(GeneratorPinWarning, match="pins zipf, which it does not use") as seen:
        Engine(sc).generate()
    assert {str(w.message).split()[3].rstrip(",") for w in seen} == {"zipf", "no_such_generator"}
    assert not any("v2probe" in str(w.message) for w in seen)
    # a version that is out of range for a name that is not used is not checked either
    assert [i.level for i in sc.validate() if i.location.startswith("generators.")] == [
        "warning",
        "warning",
    ]


def test_a_used_and_pinned_spec_gives_no_warning() -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        Engine(schema(generators={PROBE: 1})).generate()


def test_validate_reports_a_bad_pin_as_an_error_with_its_location() -> None:
    issues = [i for i in schema(generators={PROBE: 3}).validate() if i.level == "error"]
    assert [i.location for i in issues] == [f"generators.{PROBE}"]
    with pytest.raises(GenSchemaError, match="generator version 3"):
        schema(generators={PROBE: 3}).validate_or_raise()
    assert [i for i in schema(generators={PROBE: 1}).validate() if i.level == "error"] == []


def test_strategies_given_to_the_engine_are_versioned_too() -> None:
    from versioning_fixtures import TwoVersions

    sc = schema("custom")
    engine = Engine(sc, strategies={"custom": TwoVersions()})
    assert engine.generator_versions["custom"] == 2
    pinned = Engine(
        schema("custom", generators={"custom": 1}), strategies={"custom": TwoVersions()}
    )
    assert pinned.generate().tables["t"].column("x").to_pylist() == list(range(20))
    with pytest.raises(GeneratorPinError):
        Engine(schema("custom", generators={"custom": 3}), strategies={"custom": TwoVersions()})


def test_a_name_with_no_implementation_is_left_out_of_the_versions() -> None:
    doc = spec_doc("not_an_installed_plugin")
    assert "not_an_installed_plugin" not in versions.usage_of(GenSchema.from_dict(doc).tables)
    sc = GenSchema.from_dict({**doc, "generators": {"not_an_installed_plugin": 7}})
    with pytest.warns(GeneratorPinWarning, match="does not use"):
        Engine(sc)  # nothing to check the pin against: a warning, and generation fails later


def test_a_distribution_key_of_another_strategy_is_not_a_distribution_use() -> None:
    doc = spec_doc("derived")
    doc["tables"]["t"]["columns"]["x"]["generator"].update({"distribution": "normal"})
    assert set(versions.usage_of(GenSchema.from_dict(doc).tables)) == {"derived", "sequence"}
