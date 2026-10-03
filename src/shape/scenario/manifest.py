"""The run manifest: what a pack run produced, written next to its output (P6-14).

The keys are ``run_id``, ``spec_hash``, ``pack_id``, ``domain``, ``scale``, ``seed``,
``engine_version``, ``outputs``, ``tables`` (``rows``, ``columns``, ``file_paths`` each),
``validation``, ``chaos``, ``timestamps`` (``started``, ``finished``, ``elapsed_seconds``),
``workspace_id``, ``lakehouse_id``, ``sbom``, and, from manifest version 1, ``format``
(``shape-run-manifest``), ``version``, ``reproducibility`` (the tuple of
``shape.repro``, plus ``generators``: the generator version of every strategy and distribution the
run used, ``docs/GENERATION_STABILITY.md``) and ``dataset_id`` (the content address of the output
tables). The run
id is ``YYYYMMDD_HHMMSS_{domain}_{scale}_s{seed}``. A manifest written before ``format`` and
``version`` existed loads with an empty ``reproducibility`` and ``dataset_id``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shape.repro import dataset_id, reproducibility_tuple

MANIFEST_FORMAT = "shape-run-manifest"
MANIFEST_VERSION = 1
SBOM_PACKAGES = ("sqllocks-shape", "pandas", "numpy", "faker", "pyarrow", "scipy")
NOT_INSTALLED = "not installed"


class ManifestVersionError(ValueError):
    """A manifest is not one this Shape can read."""


@dataclass
class RunManifest:
    """The metadata of one run."""

    run_id: str
    spec_hash: str  # sha256 of the generation spec file (empty when the run had no spec)
    pack_id: str
    domain: str
    scale: str
    seed: int
    engine_version: str
    outputs: dict[str, Any] = field(default_factory=dict)
    tables: dict[str, dict[str, Any]] = field(default_factory=dict)
    validation: dict[str, bool] = field(default_factory=dict)
    chaos: dict[str, Any] = field(default_factory=dict)
    timestamps: dict[str, Any] = field(default_factory=dict)
    workspace_id: str = ""
    lakehouse_id: str = ""
    sbom: dict[str, str] = field(default_factory=dict)
    reproducibility: dict[str, Any] = field(default_factory=dict)
    dataset_id: str = ""

    def summary(self) -> str:
        lines = [
            f"Run Manifest: {self.run_id}",
            f"  Engine:  Shape v{self.engine_version}",
            f"  Pack:    {self.pack_id}",
            f"  Domain:  {self.domain}",
            f"  Scale:   {self.scale}",
            f"  Seed:    {self.seed}",
        ]
        if self.dataset_id:
            lines.append(f"  Dataset: {self.dataset_id}")
        if self.tables:
            total_rows = sum(int(t.get("rows", 0)) for t in self.tables.values())
            lines.append(f"  Tables:  {len(self.tables)} ({total_rows:,} total rows)")
        if self.validation:
            passed = sum(1 for v in self.validation.values() if v)
            lines.append(f"  Gates:   {passed}/{len(self.validation)} passed")
        if self.timestamps:
            lines.append(f"  Elapsed: {self.timestamps.get('elapsed_seconds', '?')}s")
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "spec_hash": self.spec_hash,
            "pack_id": self.pack_id,
            "domain": self.domain,
            "scale": self.scale,
            "seed": self.seed,
            "engine_version": self.engine_version,
            "outputs": self.outputs,
            "tables": self.tables,
            "validation": self.validation,
            "chaos": self.chaos,
            "timestamps": self.timestamps,
            "workspace_id": self.workspace_id,
            "lakehouse_id": self.lakehouse_id,
            "sbom": self.sbom,
            "format": MANIFEST_FORMAT,
            "version": MANIFEST_VERSION,
            "reproducibility": self.reproducibility,
            "dataset_id": self.dataset_id,
        }


class ManifestBuilder:
    """Build a :class:`RunManifest` during a run: ``start``, the ``record_*`` calls, ``finish``."""

    def __init__(self) -> None:
        self._m = RunManifest("", "", "", "", "", 0, "")
        self._started = 0.0
        self._started_iso = ""
        self._workspace_id = ""
        self._lakehouse_id = ""

    def start(
        self,
        spec_path: str | Path | None,
        pack: Any,
        domain_name: str,
        scale: str,
        seed: int,
    ) -> None:
        """Begin a run. ``spec_path`` is the generation spec file, hashed into ``spec_hash``."""
        from shape import __version__

        now = datetime.now(UTC)
        self._started_iso = now.isoformat()
        self._started = time.perf_counter()
        self._m = RunManifest(
            run_id=f"{now.strftime('%Y%m%d_%H%M%S')}_{domain_name}_{scale}_s{seed}",
            spec_hash=hash_file(Path(spec_path)) if spec_path is not None else "",
            pack_id=str(getattr(pack, "id", "")) if pack is not None else "",
            domain=domain_name,
            scale=scale,
            seed=seed,
            engine_version=__version__,
            sbom=collect_sbom(),
            reproducibility=reproducibility_tuple(seed, scale),
        )

    def record_output(
        self, table_name: str, rows: int, columns: int, paths: list[str] | None = None
    ) -> None:
        self._m.tables[table_name] = {"rows": rows, "columns": columns, "file_paths": paths or []}

    def record_generators(self, versions: Mapping[str, int]) -> None:
        """Record the generator version of every strategy and distribution the run used, in
        ``reproducibility`` (``generators``)."""
        self._m.reproducibility["generators"] = {k: int(v) for k, v in sorted(versions.items())}

    def record_dataset(self, tables: Mapping[str, Any]) -> None:
        """Record the dataset id of the run's output tables."""
        self._m.dataset_id = dataset_id(tables)

    def record_validation(self, gate: str, result: bool) -> None:
        self._m.validation[gate] = result

    def record_chaos(self, category: str, count: int) -> None:
        self._m.chaos[category] = self._m.chaos.get(category, 0) + count

    def record_outputs(self, outputs: dict[str, Any]) -> None:
        self._m.outputs.update(outputs)

    def set_fabric_ids(self, workspace_id: str = "", lakehouse_id: str = "") -> None:
        self._workspace_id = workspace_id
        self._lakehouse_id = lakehouse_id

    def finish(self) -> RunManifest:
        elapsed = time.perf_counter() - self._started if self._started else 0.0
        m = self._m
        m.timestamps = {
            "started": self._started_iso,
            "finished": datetime.now(UTC).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
        }
        m.workspace_id = self._workspace_id
        m.lakehouse_id = self._lakehouse_id
        return m

    # ---- serialization ---------------------------------------------------------------------

    @staticmethod
    def to_json(manifest: RunManifest) -> str:
        return json.dumps(manifest.to_dict(), indent=2, default=str)

    @staticmethod
    def to_file(manifest: RunManifest, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(ManifestBuilder.to_json(manifest), encoding="utf-8")

    @staticmethod
    def from_file(path: str | Path) -> RunManifest:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError(f"{path} is not a run manifest")
        if "format" in raw and raw["format"] != MANIFEST_FORMAT:
            raise ValueError(
                f"{path} is not a run manifest (format {raw['format']!r}, expected "
                f"{MANIFEST_FORMAT!r})"
            )
        version = raw.get("version", MANIFEST_VERSION)
        if not isinstance(version, int) or version > MANIFEST_VERSION:
            raise ManifestVersionError(
                f"{path} is run manifest version {version!r}, written by a newer Shape; this Shape "
                f"reads versions up to {MANIFEST_VERSION}. Upgrade Shape to read it"
            )
        return RunManifest(
            run_id=raw.get("run_id", ""),
            spec_hash=raw.get("spec_hash", ""),
            pack_id=raw.get("pack_id", ""),
            domain=raw.get("domain", ""),
            scale=raw.get("scale", ""),
            seed=raw.get("seed", 0),
            engine_version=raw.get("engine_version", ""),
            outputs=raw.get("outputs", {}),
            tables=raw.get("tables", {}),
            validation=raw.get("validation", {}),
            chaos=raw.get("chaos", {}),
            timestamps=raw.get("timestamps", {}),
            workspace_id=raw.get("workspace_id", ""),
            lakehouse_id=raw.get("lakehouse_id", ""),
            sbom=raw.get("sbom", {}),
            reproducibility=raw.get("reproducibility", {}),
            dataset_id=raw.get("dataset_id", ""),
        )


def collect_sbom() -> dict[str, str]:
    """The installed version of each key dependency (``not installed`` for one that is absent)."""
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as installed_version

    sbom: dict[str, str] = {}
    for package in SBOM_PACKAGES:
        try:
            sbom[package] = installed_version(package)
        except PackageNotFoundError:
            sbom[package] = NOT_INSTALLED
    return sbom


def hash_file(path: Path) -> str:
    """The SHA-256 of a file's bytes; empty when the file cannot be read."""
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""
