"""Validate a scenario pack against a domain's schema (P6-14).

The checks, and their messages, are the reference checks: entities exist in the domain, the kind
and its section agree, the gates are known. Beyond them: keys no field takes, a landing path that
would leave the output directory, an invalid chaos section, a topic that matches no table, and
simulation features that this runner does not perform are reported (the allow-list in
``benchmarks/vs_spindle/pack_1to1/`` names each one).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

from shape.scenario.loader import PACK_KINDS, ScenarioPack

KNOWN_GATES = (
    "schema_conformance",
    "referential_integrity",
    "row_count",
    "null_check",
    "uniqueness",
)
VALID_CADENCES = ("daily", "hourly", "every_15m", "every_5m", "weekly")


@dataclass
class PackValidationResult:
    """The errors and warnings of validating a pack."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        lines: list[str] = []
        if self.errors:
            lines.append(f"Errors ({len(self.errors)}):")
            lines.extend(f"  ERROR: {e}" for e in self.errors)
        if self.warnings:
            lines.append(f"Warnings ({len(self.warnings)}):")
            lines.extend(f"  WARN:  {w}" for w in self.warnings)
        if self.is_valid and not self.warnings:
            lines.append("Pack validation: PASS")
        elif self.is_valid:
            lines.append(f"Pack validation: PASS ({len(self.warnings)} warnings)")
        else:
            lines.append(f"Pack validation: FAIL ({len(self.errors)} errors)")
        return "\n".join(lines)


def unsafe_path(value: str) -> bool:
    """True for a path that would leave the directory it is joined to: absolute (either
    convention), with a drive, or with a ``..`` part."""
    posix, windows = PurePosixPath(value), PureWindowsPath(value)
    if posix.is_absolute() or windows.is_absolute() or windows.drive or value.startswith("\\"):
        return True
    return ".." in posix.parts or ".." in windows.parts


def unsafe_name(value: str) -> bool:
    """True for a name that is not one plain file-name component."""
    return not value or "/" in value or "\\" in value or value in (".", "..") or "\x00" in value


def schema_of(domain: Any) -> Any:
    """The generation schema of a loaded domain, or the schema itself."""
    return getattr(domain, "schema", domain)


def domain_name_of(domain: Any) -> str:
    schema = schema_of(domain)
    model = schema.model
    return str(getattr(domain, "name", None) or model.domain or model.name)


class PackValidator:
    """Validate a :class:`ScenarioPack` against a domain (loaded domain or generation schema)."""

    def validate(self, pack: ScenarioPack, domain: Any) -> PackValidationResult:
        result = PackValidationResult()
        try:
            schema = schema_of(domain)
            domain_tables = set(schema.table_names)
        except Exception as exc:
            result.errors.append(f"Failed to load domain schema: {exc}")
            return result

        name = domain_name_of(domain)
        if name and pack.domain and pack.domain != name:
            result.warnings.append(f"Pack domain '{pack.domain}' does not match domain '{name}'")
        if pack.pack_version < 1:
            result.errors.append(f"Invalid pack_version: {pack.pack_version}")
        if pack.kind not in PACK_KINDS:
            result.errors.append(
                f"Invalid kind '{pack.kind}'. Must be one of: {', '.join(sorted(PACK_KINDS))}"
            )

        self._entities(pack, domain_tables, result)
        self._topics(pack, domain_tables, result)
        if pack.kind == "file_drop":
            self._file_drop(pack, result)
        if pack.kind == "stream":
            self._streaming(pack, result)
        if pack.kind == "hybrid":
            self._hybrid(pack, result)
        self._targets(pack, result)
        self._gates(pack, result)
        self._extras(pack, result)
        return result

    @staticmethod
    def _entities(pack: ScenarioPack, tables: set[str], result: PackValidationResult) -> None:
        for entity in pack.entities:
            if entity not in tables:
                result.errors.append(
                    f"Entity '{entity}' referenced in pack but not found in domain schema. "
                    f"Available tables: {', '.join(sorted(tables))}"
                )

    @staticmethod
    def _topics(pack: ScenarioPack, tables: set[str], result: PackValidationResult) -> None:
        for topic in pack.topics:
            if not topic.name:
                result.errors.append("Stream topic has empty name")
            elif unsafe_name(topic.name) or (topic.event_type and unsafe_name(topic.event_type)):
                result.errors.append(
                    f"Topic '{topic.name}' (event type '{topic.event_type}') is not usable "
                    "in a file name"
                )
            if not topic.event_type:
                result.warnings.append(f"Topic '{topic.name}' has no event_type defined")
            if not topic.payload_fields:
                result.warnings.append(f"Topic '{topic.name}' has no payload_fields defined")
            if topic.name and match_table(topic.name, list(tables)) is None:
                result.warnings.append(
                    f"Topic '{topic.name}' matches no table of the domain and emits no events"
                )

    @staticmethod
    def _file_drop(pack: ScenarioPack, result: PackValidationResult) -> None:
        if pack.file_drop is None:
            result.errors.append("Pack kind is 'file_drop' but no file_drop section defined")
            return
        if pack.file_drop.cadence not in VALID_CADENCES:
            result.warnings.append(
                f"Unusual cadence '{pack.file_drop.cadence}'. "
                f"Common values: {', '.join(sorted(VALID_CADENCES))}"
            )
        if not pack.file_drop.entities:
            result.warnings.append("file_drop section has no entities listed")
        if not pack.file_drop.formats:
            result.errors.append("file_drop section has no formats defined")

    @staticmethod
    def _streaming(pack: ScenarioPack, result: PackValidationResult) -> None:
        if pack.streaming is None:
            result.errors.append("Pack kind is 'stream' but no streaming section defined")
            return
        if not pack.streaming.topics:
            result.warnings.append("streaming section has no topics defined")
        if pack.streaming.cadence and pack.streaming.cadence.rate_per_sec <= 0:
            result.errors.append("Streaming rate_per_sec must be positive")

    @staticmethod
    def _hybrid(pack: ScenarioPack, result: PackValidationResult) -> None:
        if pack.hybrid is None:
            result.errors.append("Pack kind is 'hybrid' but no hybrid section defined")
            return
        if pack.hybrid.micro_batch is None and pack.hybrid.stream is None:
            result.errors.append("Hybrid pack must define at least micro_batch or stream")

    @staticmethod
    def _targets(pack: ScenarioPack, result: PackValidationResult) -> None:
        if not pack.fabric_targets:
            result.warnings.append(
                "No fabric_targets defined — pack cannot target Fabric resources"
            )
        root = pack.fabric_targets.get("lakehouse_files_root")
        if root is not None and (not isinstance(root, str) or unsafe_path(root)):
            result.errors.append(
                f"fabric_targets.lakehouse_files_root {root!r} must be a relative path inside "
                "the output directory (no leading slash, drive or '..')"
            )

    @staticmethod
    def _gates(pack: ScenarioPack, result: PackValidationResult) -> None:
        if pack.validation is None:
            return
        for gate in pack.validation.required_gates:
            if gate not in KNOWN_GATES:
                result.warnings.append(
                    f"Unknown validation gate '{gate}'. "
                    f"Known gates: {', '.join(sorted(KNOWN_GATES))}"
                )

    @staticmethod
    def _extras(pack: ScenarioPack, result: PackValidationResult) -> None:
        for key in pack.extra_keys:
            result.warnings.append(f"Unknown key '{key}' is ignored")
        if pack.chaos is not None and pack.chaos.get("enabled", False):
            for message in chaos_config(pack.chaos).validate():
                result.errors.append(f"chaos: {message}")
        fd = pack.file_drop
        if fd is not None:
            for label, spec in (
                ("lateness", fd.lateness),
                ("duplicates", fd.duplicates),
                ("backfill", fd.backfill),
            ):
                if spec is not None and spec.enabled:
                    result.warnings.append(
                        f"file_drop.{label} is enabled but a pack run does not simulate it"
                    )
        if pack.failure_injection is not None and pack.failure_injection.enabled:
            result.warnings.append(
                "failure_injection is enabled but a pack run does not apply it; use `chaos`"
            )


def match_table(name: str, tables: list[str]) -> str | None:
    """The table a topic name stands for: the same name, else the first table (in order) whose
    name contains the topic's or is contained in it."""
    if name in tables:
        return name
    for table in tables:
        if name in table or table in name:
            return table
    return None


def chaos_config(section: dict[str, Any]) -> Any:
    """A ``ChaosConfig`` from a ``chaos`` mapping (a pack's or a spec's): ``enabled``,
    ``intensity``, ``seed``, ``warmup_days``, ``chaos_start_day``, ``escalation``,
    ``breaking_change_day`` and ``categories``; other keys are not chaos settings."""
    from shape.chaos import ChaosConfig

    config = ChaosConfig(enabled=bool(section.get("enabled", False)))
    for key in ("intensity", "escalation"):
        if key in section:
            setattr(config, key, str(section[key]))
    for key in ("seed", "warmup_days", "chaos_start_day", "breaking_change_day"):
        if key in section:
            setattr(config, key, int(section[key]))
    if "chaos_start_day" not in section and config.chaos_start_day <= config.warmup_days:
        config.chaos_start_day = config.warmup_days + 1
    if isinstance(section.get("categories"), dict):
        config.categories = {
            str(k): dict(v) if isinstance(v, dict) else {"enabled": bool(v), "weight": 0.1}
            for k, v in section["categories"].items()
        }
    return config
