"""Generation Spec Language (GSL): one YAML file that ties a schema, a scenario pack, chaos,
outputs and validation gates together (P6-14).

A relative path in a spec (the pack, a schema file) is resolved against the spec file's
directory. The schema type is ``domain`` (an installed domain) or ``schema_file`` (a generation
schema file, as ``shape from-ddl`` writes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.scenario.loader import PackError, Reader, describe_type, read_yaml

SCHEMA_TYPES = ("domain", "schema_file")
DEFAULT_DRIFT_POLICY = "quarantine_on_breaking_change"
LAKEHOUSE_MODES = ("tables_and_files", "tables_only", "files_only")


@dataclass
class SchemaRef:
    type: str = "domain"
    path: str | None = None
    domain: str | None = None


@dataclass
class DateRangeSpec:
    start: str = ""
    end: str = ""


@dataclass
class ScenarioRef:
    pack: str = ""
    scale: str = "small"
    seed: int = 42
    date_range: DateRangeSpec | None = None
    #: The run's identifier mode (``reserved`` or ``realistic``); None keeps the schema's.
    identifiers: str | None = None


@dataclass
class ChaosSpec:
    enabled: bool = False
    intensity: str = "moderate"
    config: dict[str, Any] = field(default_factory=dict)


@dataclass
class LandingZoneSpec:
    root: str = ""


@dataclass
class LakehouseOutputSpec:
    mode: str = "tables_and_files"
    tables: list[str] = field(default_factory=list)
    landing_zone: LandingZoneSpec | None = None


@dataclass
class EventstreamTopicSpec:
    name: str = ""
    event_type: str = ""


@dataclass
class EventstreamOutputSpec:
    enabled: bool = False
    endpoint_secret_ref: str = ""
    topic_prefix: str = ""
    topics: list[EventstreamTopicSpec] = field(default_factory=list)


@dataclass
class OutputsSpec:
    lakehouse: LakehouseOutputSpec | None = None
    eventstream: EventstreamOutputSpec | None = None


@dataclass
class ValidationGateSpec:
    gates: list[str] = field(default_factory=list)
    drift_policy: str = DEFAULT_DRIFT_POLICY


@dataclass
class GenerationSpec:
    """A parsed GSL document. ``path`` is the spec file (None for a dict); ``extra_keys`` lists
    the keys no field takes."""

    version: int = 1
    name: str = ""
    schema: SchemaRef | None = None
    scenario: ScenarioRef | None = None
    chaos: ChaosSpec | None = None
    outputs: OutputsSpec | None = None
    validation: ValidationGateSpec | None = None
    path: Path | None = None
    extra_keys: list[str] = field(default_factory=list)
    needs_release: str | None = None  # the release the file says reads it (newer files)
    _base_dir: Path = field(default_factory=lambda: Path("."), repr=False)

    def resolve_path(self, relative: str) -> Path:
        """``relative`` resolved against the spec file's directory."""
        p = Path(relative)
        return p if p.is_absolute() else (self._base_dir / p).resolve()


class GSLParser:
    """Parse GSL YAML into a :class:`GenerationSpec`."""

    def parse(self, path: str | Path) -> GenerationSpec:
        """Parse a spec file; relative paths in it resolve against its directory."""
        resolved = Path(path).resolve()
        raw = read_yaml(resolved, "GSL spec")
        if raw is None:
            raise PackError(f"GSL spec {resolved} is empty")
        try:
            spec = self._parse(raw, resolved.parent)
        except PackError as exc:
            raise PackError(f"{resolved}: {exc}") from exc
        spec.path = resolved
        return spec

    def parse_dict(self, raw: dict[str, Any], base_dir: str | Path = ".") -> GenerationSpec:
        """Parse a spec from a mapping (tests)."""
        return self._parse(raw, Path(base_dir))

    def _parse(self, raw: Any, base_dir: Path) -> GenerationSpec:
        unknown: list[str] = []
        r = Reader(raw, "", unknown)
        spec = GenerationSpec(
            version=r.declare("generation-spec"),
            name=r.text("name", ""),
            schema=self._schema(r.section("schema")),
            scenario=self._scenario(r.section("scenario")),
            chaos=self._chaos(r.section("chaos")),
            outputs=self._outputs(r.section("outputs")),
            validation=self._validation(r.section("validation")),
            extra_keys=unknown,
            needs_release=r.needs_release,
            _base_dir=base_dir,
        )
        r.close()
        return spec

    @staticmethod
    def _schema(r: Reader | None) -> SchemaRef | None:
        if r is None:
            return None
        ref = SchemaRef(
            r.text("type", "domain"), r.optional_text("path"), r.optional_text("domain")
        )
        r.close()
        return ref

    @staticmethod
    def _scenario(r: Reader | None) -> ScenarioRef | None:
        if r is None:
            return None
        date_range = None
        dr = r.section("date_range")
        if dr is not None:
            date_range = DateRangeSpec(dr.text("start", ""), dr.text("end", ""))
            dr.close()
        ref = ScenarioRef(
            pack=r.text("pack", ""),
            scale=r.text("scale", "small"),
            seed=r.integer("seed", 42),
            date_range=date_range,
            identifiers=r.optional_text("identifiers"),
        )
        r.close()
        if ref.identifiers is not None:
            from shape.generation.identifiers import check_identifiers

            try:
                check_identifiers(ref.identifiers, "scenario.identifiers")
            except ValueError as exc:
                raise PackError(str(exc)) from None
        return ref

    @staticmethod
    def _chaos(r: Reader | None) -> ChaosSpec | None:
        if r is None:
            return None
        # Everything but the two named keys is chaos configuration; a nested `config:` mapping
        # (as the tutorial writes it) is part of it, not a setting called "config".
        config = {k: r.get(k) for k in list(r.raw) if k not in ("enabled", "intensity")}
        nested = config.pop("config", None)
        if isinstance(nested, dict):
            config = {**config, **nested}
        elif nested is not None:
            raise PackError(f"{r.path}.config must be a mapping, got {describe_type(nested)}")
        spec = ChaosSpec(r.flag("enabled", False), r.text("intensity", "moderate"), config)
        r.close()
        return spec

    def _outputs(self, r: Reader | None) -> OutputsSpec | None:
        if r is None:
            return None
        spec = OutputsSpec(
            lakehouse=self._lakehouse(r.section("lakehouse")),
            eventstream=self._eventstream(r.section("eventstream")),
        )
        r.close()
        return spec

    @staticmethod
    def _lakehouse(r: Reader | None) -> LakehouseOutputSpec | None:
        if r is None:
            return None
        landing = None
        lz = r.section("landing_zone")
        if lz is not None:
            landing = LandingZoneSpec(lz.text("root", ""))
            lz.close()
        r.get("formats")  # listed in specs; the pack's formats decide what is written
        spec = LakehouseOutputSpec(
            mode=r.text("mode", "tables_and_files"),
            tables=r.strings("tables"),
            landing_zone=landing,
        )
        r.close()
        return spec

    @staticmethod
    def _eventstream(r: Reader | None) -> EventstreamOutputSpec | None:
        if r is None:
            return None
        topics = []
        for t in r.sections("topics"):
            topics.append(EventstreamTopicSpec(t.text("name", ""), t.text("event_type", "")))
            t.close()
        spec = EventstreamOutputSpec(
            enabled=r.flag("enabled", False),
            endpoint_secret_ref=r.text("endpoint_secret_ref", ""),
            topic_prefix=r.text("topic_prefix", ""),
            topics=topics,
        )
        r.close()
        return spec

    @staticmethod
    def _validation(r: Reader | None) -> ValidationGateSpec | None:
        if r is None:
            return None
        spec = ValidationGateSpec(r.strings("gates"), r.text("drift_policy", DEFAULT_DRIFT_POLICY))
        r.close()
        return spec


def is_spec_document(path: str | Path) -> bool:
    """True for a GSL document: a YAML mapping with a ``scenario`` section (a pack has none)."""
    raw = read_yaml(Path(path), "Document")
    return isinstance(raw, dict) and "scenario" in raw and "kind" not in raw
