"""Resolve and validate a generation spec: find its pack and its domain, and check both (P6-14)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape.scenario.gsl import (
    LAKEHOUSE_MODES,
    SCHEMA_TYPES,
    GenerationSpec,
)
from shape.scenario.loader import PackError, PackLoader, ScenarioPack
from shape.scenario.runner import _with_spec
from shape.scenario.validator import (
    KNOWN_GATES,
    PackValidationResult,
    PackValidator,
    chaos_config,
    schema_of,
    unsafe_path,
)


def load_pack_domain(name: str, mode: str | None = None) -> Any:
    """The installed domain ``name`` (see ``shape list``)."""
    from shape.generation.domains import load_domain

    return load_domain(name, mode=mode)


def load_schema_file(path: Path) -> Any:
    """A generation schema file (what ``shape from-ddl`` writes), as a domain-like object."""
    import json

    from shape.generation.schema import GenSchema

    return GenSchema.from_dict(json.loads(path.read_text(encoding="utf-8")))


def spec_domain(spec: GenerationSpec) -> Any:
    """The domain or schema a spec names (a ``PackError`` when it names none)."""
    ref = spec.schema
    if ref is None:
        raise PackError("the spec has no schema section")
    if ref.type == "domain":
        if not ref.domain:
            raise PackError("schema.domain is missing")
        return load_pack_domain(ref.domain)
    if ref.type == "schema_file":
        if not ref.path:
            raise PackError("schema.path is missing")
        target = spec.resolve_path(ref.path)
        if not target.is_file():
            raise PackError(f"schema file not found: {target}")
        return load_schema_file(target)
    raise PackError(f"unknown schema type {ref.type!r}; choose one of: {', '.join(SCHEMA_TYPES)}")


def spec_pack(spec: GenerationSpec) -> ScenarioPack:
    """The scenario pack a spec points at (a path, relative to the spec file)."""
    if spec.scenario is None or not spec.scenario.pack:
        raise PackError("the spec has no scenario.pack")
    target = spec.resolve_path(spec.scenario.pack)
    return PackLoader().load(target)


def validate_spec(spec: GenerationSpec) -> PackValidationResult:
    """Everything checkable without running: schema, pack, scale, chaos, gates and outputs."""
    result = PackValidationResult()
    if spec.version != 1:
        result.errors.append(f"Unsupported spec version {spec.version}; this is version 1")
    domain: Any = None
    try:
        domain = spec_domain(spec)
    except Exception as exc:
        result.errors.append(f"schema: {exc}")
    pack: ScenarioPack | None = None
    try:
        pack = spec_pack(spec)
    except Exception as exc:
        result.errors.append(f"scenario: {exc}")
    if spec.scenario is not None and domain is not None:
        presets = schema_of(domain).generation.scales
        if presets and spec.scenario.scale not in presets:
            result.errors.append(
                f"scenario.scale {spec.scenario.scale!r} is not a preset of the domain "
                f"({', '.join(presets)})"
            )
    if pack is not None and domain is not None:
        checked = PackValidator().validate(_with_spec(pack, spec), domain)
        result.errors.extend(f"pack: {e}" for e in checked.errors)
        result.warnings.extend(f"pack: {w}" for w in checked.warnings)
    if spec.chaos is not None and spec.chaos.enabled:
        section = {**spec.chaos.config, "enabled": True, "intensity": spec.chaos.intensity}
        result.errors.extend(f"chaos: {m}" for m in chaos_config(section).validate())
    if spec.validation is not None:
        for gate in spec.validation.gates:
            if gate not in KNOWN_GATES:
                result.warnings.append(
                    f"Unknown validation gate '{gate}'. "
                    f"Known gates: {', '.join(sorted(KNOWN_GATES))}"
                )
    lake = spec.outputs.lakehouse if spec.outputs else None
    if lake is not None:
        if lake.mode not in LAKEHOUSE_MODES:
            result.errors.append(
                f"outputs.lakehouse.mode {lake.mode!r} must be one of: {', '.join(LAKEHOUSE_MODES)}"
            )
        elif lake.mode != "files_only" or lake.tables:
            pass
        root = lake.landing_zone.root if lake.landing_zone else ""
        if root and unsafe_path(root):
            result.errors.append(
                f"outputs.lakehouse.landing_zone.root {root!r} must be a relative path "
                "(no leading slash, drive or '..')"
            )
    if spec.outputs is not None and spec.outputs.eventstream is not None:
        if spec.outputs.eventstream.enabled:
            result.warnings.append(
                "outputs.eventstream is enabled but a spec run does not send events; "
                "use `shape emit`"
            )
    for key in spec.extra_keys:
        result.warnings.append(f"Unknown key '{key}' is ignored")
    return result
