"""Scenario packs: the YAML document, its dataclasses and the loader (P6-14).

A pack bundles a domain, a simulation kind (``file_drop``, ``stream`` or ``hybrid``), optional
chaos, validation gates and the landing paths of a run. The structure and defaults follow the
reference inputs in ``tests/fixtures/packs/``. Unlike a plain port, the loader refuses a document
that is not a mapping or has a value of the wrong type, and records every key it does not know so
that the validator can warn about it (a mistyped key never silently changes nothing).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

PACK_KINDS = ("file_drop", "stream", "hybrid")


class PackError(ShapeError, ValueError):
    """A scenario pack or generation spec document is unreadable or malformed."""


# ---- nested specs: they mirror the YAML structure ------------------------------------------


@dataclass
class ManifestSpec:
    enabled: bool = True
    name: str = "manifest_{dt}.json"


@dataclass
class DoneFlagSpec:
    enabled: bool = True
    name: str = "done_{dt}.flag"


@dataclass
class LatenessSpec:
    enabled: bool = False
    probability: float = 0.0
    max_days_late: int = 0


@dataclass
class DuplicateSpec:
    enabled: bool = False
    probability: float = 0.0


@dataclass
class BackfillSpec:
    enabled: bool = False
    max_days_back: int = 0


@dataclass
class FileDropSpec:
    cadence: str = "daily"
    partitioning: str = "dt=YYYY-MM-DD"
    formats: list[str] = field(default_factory=lambda: ["parquet"])
    file_naming: str = "{domain}_{entity}_{dt}_{seq}.parquet"
    entities: list[str] = field(default_factory=list)
    manifest: ManifestSpec | None = None
    done_flag: DoneFlagSpec | None = None
    lateness: LatenessSpec | None = None
    duplicates: DuplicateSpec | None = None
    backfill: BackfillSpec | None = None


@dataclass
class StreamEnvelopeSpec:
    schemaVersion: str = "1.0"  # noqa: N815 - the key as written in the document
    fields: list[str] = field(default_factory=list)


@dataclass
class StreamCadenceSpec:
    rate_per_sec: float = 10.0
    realtime: bool = True
    jitter_ms: int = 0
    burst: dict[str, Any] | None = None


@dataclass
class StreamOrderingSpec:
    out_of_order_probability: float = 0.0
    max_delay_seconds: int = 0


@dataclass
class StreamReplaySpec:
    enabled: bool = False
    window_minutes: int = 15


@dataclass
class StreamTopicSpec:
    name: str = ""
    event_type: str = ""
    payload_fields: list[str] = field(default_factory=list)


@dataclass
class StreamAnomalySpec:
    enabled: bool = False
    types: list[str] = field(default_factory=list)


@dataclass
class StreamSpec:
    envelope: StreamEnvelopeSpec | None = None
    cadence: StreamCadenceSpec | None = None
    ordering: StreamOrderingSpec | None = None
    replay: StreamReplaySpec | None = None
    topics: list[StreamTopicSpec] = field(default_factory=list)
    anomalies: StreamAnomalySpec | None = None


@dataclass
class HybridMicroBatchSpec:
    cadence: str = "every_15m"
    formats: list[str] = field(default_factory=lambda: ["jsonl"])
    partitioning: str = "dt=YYYY-MM-DD/hour=HH"
    entities: list[str] = field(default_factory=list)


@dataclass
class HybridStreamSpec:
    rate_per_sec: float = 10.0
    topics: list[StreamTopicSpec] = field(default_factory=list)


@dataclass
class HybridLinkStrategySpec:
    correlation_id: bool = True
    natural_keys: bool = True


@dataclass
class HybridSpec:
    stream_to: str = "eventhouse"
    micro_batch_to: str = "lakehouse_files"
    micro_batch: HybridMicroBatchSpec | None = None
    stream: HybridStreamSpec | None = None
    link_strategy: HybridLinkStrategySpec | None = None


@dataclass
class SchemaDriftSpec:
    enabled: bool = False
    mode: str = "additive"
    breaking_change_day: int = 0


@dataclass
class FailureInjectionSpec:
    enabled: bool = False
    corrupt_file_probability: float = 0.0
    partial_write_probability: float = 0.0
    schema_drift: SchemaDriftSpec | None = None


@dataclass
class ValidationSpec:
    required_gates: list[str] = field(default_factory=list)
    quarantine_folder: str | None = None


@dataclass
class ScenarioPack:
    """A complete scenario pack. ``extra_keys`` lists the keys (dotted) that no field takes."""

    pack_version: int
    id: str
    kind: str  # file_drop | stream | hybrid
    domain: str
    description: str
    fabric_targets: dict[str, Any]
    file_drop: FileDropSpec | None = None
    streaming: StreamSpec | None = None
    hybrid: HybridSpec | None = None
    failure_injection: FailureInjectionSpec | None = None
    validation: ValidationSpec | None = None
    chaos: dict[str, Any] | None = None
    extra_keys: list[str] = field(default_factory=list)

    @property
    def entities(self) -> list[str]:
        """The entities (tables) the pack lists."""
        if self.file_drop and self.file_drop.entities:
            return self.file_drop.entities
        if self.hybrid and self.hybrid.micro_batch and self.hybrid.micro_batch.entities:
            return self.hybrid.micro_batch.entities
        return []

    @property
    def topics(self) -> list[StreamTopicSpec]:
        """The stream topics the pack lists."""
        if self.streaming and self.streaming.topics:
            return self.streaming.topics
        if self.hybrid and self.hybrid.stream and self.hybrid.stream.topics:
            return self.hybrid.stream.topics
        return []


# ---- reading a mapping ---------------------------------------------------------------------


class Reader:
    """One mapping of the document, read key by key: a wrong type is an error naming the key,
    and the keys never read are recorded as unknown when the section is closed."""

    def __init__(self, raw: Any, path: str, unknown: list[str]) -> None:
        if not isinstance(raw, dict):
            raise PackError(f"{path or 'document'} must be a mapping, got {_kind(raw)}")
        self.raw: dict[str, Any] = raw
        self.path = path
        self._unknown = unknown
        self._seen: set[str] = set()

    def _where(self, key: str) -> str:
        return f"{self.path}.{key}" if self.path else key

    def get(self, key: str, default: Any = None) -> Any:
        self._seen.add(key)
        return self.raw.get(key, default)

    def _typed(self, key: str, default: Any, kinds: tuple[type, ...], what: str) -> Any:
        value = self.get(key, default)
        if value is None:
            return default
        if isinstance(value, bool) and bool not in kinds:
            raise PackError(f"{self._where(key)} must be {what}, got {_kind(value)}")
        if not isinstance(value, kinds):
            raise PackError(f"{self._where(key)} must be {what}, got {_kind(value)}")
        return value

    def text(self, key: str, default: str = "") -> str:
        return str(self._typed(key, default, (str, int, float), "text"))

    def optional_text(self, key: str) -> str | None:
        value = self.get(key)
        return None if value is None else self.text(key)

    def integer(self, key: str, default: int = 0) -> int:
        return int(self._typed(key, default, (int,), "an integer"))

    def number(self, key: str, default: float = 0.0) -> float:
        return float(self._typed(key, default, (int, float), "a number"))

    def flag(self, key: str, default: bool = False) -> bool:
        return bool(self._typed(key, default, (bool,), "true or false"))

    def strings(self, key: str, default: list[str] | None = None) -> list[str]:
        value = self.get(key)
        if value is None:
            return list(default or [])
        if not isinstance(value, list) or not all(isinstance(v, (str, int)) for v in value):
            raise PackError(f"{self._where(key)} must be a list of names, got {_kind(value)}")
        return [str(v) for v in value]

    def mapping(self, key: str) -> dict[str, Any] | None:
        value = self.get(key)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise PackError(f"{self._where(key)} must be a mapping, got {_kind(value)}")
        return value

    def section(self, key: str) -> Reader | None:
        value = self.get(key)
        return None if value is None else Reader(value, self._where(key), self._unknown)

    def sections(self, key: str) -> list[Reader]:
        value = self.get(key)
        if value is None:
            return []
        if not isinstance(value, list):
            raise PackError(f"{self._where(key)} must be a list, got {_kind(value)}")
        return [Reader(v, f"{self._where(key)}[{i}]", self._unknown) for i, v in enumerate(value)]

    def close(self) -> None:
        for key in self.raw:
            if key not in self._seen:
                self._unknown.append(self._where(str(key)))


def _kind(value: Any) -> str:
    if value is None:
        return "nothing"
    return {
        dict: "a mapping",
        list: "a list",
        str: "text",
        bool: "true or false",
        int: "an integer",
        float: "a number",
    }.get(type(value), type(value).__name__)


def read_yaml(path: Path, what: str) -> Any:
    """The parsed YAML of ``path``; a clear error for a missing file or invalid YAML."""
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - PyYAML is in the dev and yaml extras
        raise ImportError(
            "scenario packs are YAML: install PyYAML (pip install 'sqllocks-shape[yaml]')"
        ) from exc

    if not path.is_file():
        raise FileNotFoundError(f"{what} not found: {path}")
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise PackError(f"{what} {path} is not valid YAML: {exc}") from exc


# ---- the loader ----------------------------------------------------------------------------


class PackLoader:
    """Load scenario pack YAML files into :class:`ScenarioPack` instances.

    Shape ships no packs of its own: ``root`` is a directory laid out as
    ``<root>/<domain>/<pack_id>.yaml`` that the caller supplies.
    """

    def __init__(self, root: str | Path | None = None) -> None:
        self._root = Path(root) if root else None

    def load(self, path: str | Path) -> ScenarioPack:
        """Load a scenario pack from a YAML file."""
        path = Path(path)
        raw = read_yaml(path, "Scenario pack")
        if raw is None:
            raise PackError(f"Scenario pack {path} is empty")
        try:
            return self.parse(raw)
        except PackError as exc:
            raise PackError(f"{path}: {exc}") from exc

    def load_from_root(self, domain: str, pack_id: str) -> ScenarioPack:
        """Load ``<root>/<domain>/<pack_id>.yaml``."""
        if self._root is None or not self._root.is_dir():
            raise FileNotFoundError(
                "no pack root: Shape ships no packs; give a directory (`--root`) laid out as "
                "<root>/<domain>/<pack_id>.yaml"
            )
        for part in (domain, pack_id):
            if not part or "/" in part or "\\" in part or part in (".", ".."):
                raise PackError(f"not a pack name: {part!r}")
        pack_dir = self._root / domain
        if not pack_dir.is_dir():
            raise FileNotFoundError(
                f"No packs for domain '{domain}'. Available: {', '.join(self._domains())}"
            )
        pack_file = pack_dir / f"{pack_id}.yaml"
        if not pack_file.is_file():
            available = sorted(f.stem for f in pack_dir.glob("*.yaml"))
            raise FileNotFoundError(
                f"Pack '{pack_id}' not found in domain '{domain}'. "
                f"Available: {', '.join(available)}"
            )
        return self.load(pack_file)

    def list_packs(self) -> list[dict[str, str]]:
        """``domain``, ``pack_id`` and ``path`` of every pack under the root."""
        results: list[dict[str, str]] = []
        if self._root is None or not self._root.is_dir():
            return results
        for domain_dir in sorted(self._root.iterdir()):
            if not domain_dir.is_dir():
                continue
            for yaml_file in sorted(domain_dir.glob("*.yaml")):
                results.append(
                    {"domain": domain_dir.name, "pack_id": yaml_file.stem, "path": str(yaml_file)}
                )
        return results

    def _domains(self) -> list[str]:
        if self._root is None or not self._root.is_dir():
            return []
        return sorted(d.name for d in self._root.iterdir() if d.is_dir())

    # ---- parsing ---------------------------------------------------------------------------

    def parse(self, raw: Any) -> ScenarioPack:
        """A pack from an already-parsed document."""
        unknown: list[str] = []
        r = Reader(raw, "", unknown)
        pack = ScenarioPack(
            pack_version=r.integer("pack_version", 1),
            id=r.text("id", "unknown"),
            kind=r.text("kind", "file_drop"),
            domain=r.text("domain", ""),
            description=r.text("description", ""),
            fabric_targets=r.mapping("fabric_targets") or {},
            file_drop=self._file_drop(r.section("file_drop")),
            streaming=self._streaming(r.section("streaming")),
            hybrid=self._hybrid(r.section("hybrid")),
            failure_injection=self._failure(r.section("failure_injection")),
            validation=self._validation(r.section("validation")),
            chaos=r.mapping("chaos"),
        )
        r.close()
        pack.extra_keys = unknown
        return pack

    def _file_drop(self, r: Reader | None) -> FileDropSpec | None:
        if r is None:
            return None
        spec = FileDropSpec(
            cadence=r.text("cadence", "daily"),
            partitioning=r.text("partitioning", "dt=YYYY-MM-DD"),
            formats=r.strings("formats", ["parquet"]),
            file_naming=r.text("file_naming", "{domain}_{entity}_{dt}_{seq}.parquet"),
            entities=r.strings("entities"),
            manifest=self._manifest(r.section("manifest")),
            done_flag=self._done_flag(r.section("done_flag")),
            lateness=self._lateness(r.section("lateness")),
            duplicates=self._duplicates(r.section("duplicates")),
            backfill=self._backfill(r.section("backfill")),
        )
        r.close()
        return spec

    @staticmethod
    def _manifest(r: Reader | None) -> ManifestSpec | None:
        if r is None:
            return None
        spec = ManifestSpec(r.flag("enabled", True), r.text("name", "manifest_{dt}.json"))
        r.close()
        return spec

    @staticmethod
    def _done_flag(r: Reader | None) -> DoneFlagSpec | None:
        if r is None:
            return None
        spec = DoneFlagSpec(r.flag("enabled", True), r.text("name", "done_{dt}.flag"))
        r.close()
        return spec

    @staticmethod
    def _lateness(r: Reader | None) -> LatenessSpec | None:
        if r is None:
            return None
        spec = LatenessSpec(
            r.flag("enabled", False), r.number("probability", 0.0), r.integer("max_days_late", 0)
        )
        r.close()
        return spec

    @staticmethod
    def _duplicates(r: Reader | None) -> DuplicateSpec | None:
        if r is None:
            return None
        spec = DuplicateSpec(r.flag("enabled", False), r.number("probability", 0.0))
        r.close()
        return spec

    @staticmethod
    def _backfill(r: Reader | None) -> BackfillSpec | None:
        if r is None:
            return None
        spec = BackfillSpec(r.flag("enabled", False), r.integer("max_days_back", 0))
        r.close()
        return spec

    def _streaming(self, r: Reader | None) -> StreamSpec | None:
        if r is None:
            return None
        spec = StreamSpec(
            envelope=self._envelope(r.section("envelope")),
            cadence=self._cadence(r.section("cadence")),
            ordering=self._ordering(r.section("ordering")),
            replay=self._replay(r.section("replay")),
            topics=[self._topic(t) for t in r.sections("topics")],
            anomalies=self._anomalies(r.section("anomalies")),
        )
        r.close()
        return spec

    @staticmethod
    def _envelope(r: Reader | None) -> StreamEnvelopeSpec | None:
        if r is None:
            return None
        spec = StreamEnvelopeSpec(r.text("schemaVersion", "1.0"), r.strings("fields"))
        r.close()
        return spec

    @staticmethod
    def _cadence(r: Reader | None) -> StreamCadenceSpec | None:
        if r is None:
            return None
        spec = StreamCadenceSpec(
            r.number("rate_per_sec", 10.0),
            r.flag("realtime", True),
            r.integer("jitter_ms", 0),
            r.mapping("burst"),
        )
        r.close()
        return spec

    @staticmethod
    def _ordering(r: Reader | None) -> StreamOrderingSpec | None:
        if r is None:
            return None
        spec = StreamOrderingSpec(
            r.number("out_of_order_probability", 0.0), r.integer("max_delay_seconds", 0)
        )
        r.close()
        return spec

    @staticmethod
    def _replay(r: Reader | None) -> StreamReplaySpec | None:
        if r is None:
            return None
        spec = StreamReplaySpec(r.flag("enabled", False), r.integer("window_minutes", 15))
        r.close()
        return spec

    @staticmethod
    def _topic(r: Reader) -> StreamTopicSpec:
        spec = StreamTopicSpec(
            r.text("name", ""), r.text("event_type", ""), r.strings("payload_fields")
        )
        r.close()
        return spec

    @staticmethod
    def _anomalies(r: Reader | None) -> StreamAnomalySpec | None:
        if r is None:
            return None
        spec = StreamAnomalySpec(r.flag("enabled", False), r.strings("types"))
        r.close()
        return spec

    def _hybrid(self, r: Reader | None) -> HybridSpec | None:
        if r is None:
            return None
        micro = None
        mb = r.section("micro_batch")
        if mb is not None:
            micro = HybridMicroBatchSpec(
                cadence=mb.text("cadence", "every_15m"),
                formats=mb.strings("formats", ["jsonl"]),
                partitioning=mb.text("partitioning", "dt=YYYY-MM-DD/hour=HH"),
                entities=mb.strings("entities"),
            )
            mb.close()
        stream = None
        st = r.section("stream")
        if st is not None:
            stream = HybridStreamSpec(
                rate_per_sec=st.number("rate_per_sec", 10.0),
                topics=[self._topic(t) for t in st.sections("topics")],
            )
            st.close()
        link = None
        ls = r.section("link_strategy")
        if ls is not None:
            link = HybridLinkStrategySpec(
                ls.flag("correlation_id", True), ls.flag("natural_keys", True)
            )
            ls.close()
        spec = HybridSpec(
            stream_to=r.text("stream_to", "eventhouse"),
            micro_batch_to=r.text("micro_batch_to", "lakehouse_files"),
            micro_batch=micro,
            stream=stream,
            link_strategy=link,
        )
        r.close()
        return spec

    @staticmethod
    def _failure(r: Reader | None) -> FailureInjectionSpec | None:
        if r is None:
            return None
        drift = None
        sd = r.section("schema_drift")
        if sd is not None:
            drift = SchemaDriftSpec(
                sd.flag("enabled", False),
                sd.text("mode", "additive"),
                sd.integer("breaking_change_day", 0),
            )
            sd.close()
        spec = FailureInjectionSpec(
            enabled=r.flag("enabled", False),
            corrupt_file_probability=r.number("corrupt_file_probability", 0.0),
            partial_write_probability=r.number("partial_write_probability", 0.0),
            schema_drift=drift,
        )
        r.close()
        return spec

    @staticmethod
    def _validation(r: Reader | None) -> ValidationSpec | None:
        if r is None:
            return None
        spec = ValidationSpec(r.strings("required_gates"), r.optional_text("quarantine_folder"))
        r.close()
        return spec
