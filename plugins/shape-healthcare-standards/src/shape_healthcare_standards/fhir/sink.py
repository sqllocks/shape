"""FHIR R4 sinks: Bulk-FHIR style NDJSON files and JSON ``collection`` Bundles."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from ..common import build_tables, output_path
from ..contract import ContractError
from .resources import TABLE_RESOURCES, Resource, resources_for

BUNDLE_BASE_URL = "https://example.org/fhir"


def dumps(resource: Resource) -> str:
    """One resource as compact UTF-8 JSON on a single line."""
    return json.dumps(resource, ensure_ascii=False, separators=(",", ":"))


def write_atomic(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a temporary file and a rename."""
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def resource_stream(
    table: str, batches: Iterable[pa.RecordBatch], options: Mapping[str, Any]
) -> Iterator[Resource]:
    """The resources of the primary table, completed from the ``tables`` companions."""
    if table not in TABLE_RESOURCES:
        raise ContractError(
            f"table {table!r} has no FHIR R4 mapping; tables: {sorted(TABLE_RESOURCES)}"
        )
    return resources_for(table, build_tables(table, batches, options.get("tables")))


class FhirNdjsonSink:
    """One ``<ResourceType>.ndjson`` file per resource type in the output directory."""

    name = "fhir-ndjson"
    schemes = ("file",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        by_type: dict[str, list[str]] = {t: [] for t in TABLE_RESOURCES.get(table, ())}
        count = 0
        for res in resource_stream(table, batches, options):
            by_type.setdefault(res["resourceType"], []).append(dumps(res))
            count += 1
        out = output_path(uri)
        out.mkdir(parents=True, exist_ok=True)
        for rtype, lines in by_type.items():
            if lines:
                write_atomic(out / f"{rtype}.ndjson", "\n".join(lines) + "\n")
        return count


class FhirBundleSink:
    """JSON Bundles of type ``collection``: ``bundle-000001.json``, ``bundle-000002.json``, ...

    Option ``bundle_size`` (default 100) is the most resources in one Bundle.
    """

    name = "fhir-bundle"
    schemes = ("file",)

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        size = int(options.get("bundle_size", 100))
        if size < 1:
            raise ValueError("bundle_size must be at least 1")
        base = str(options.get("base_url", BUNDLE_BASE_URL)).rstrip("/")
        resources = list(resource_stream(table, batches, options))
        out = output_path(uri)
        out.mkdir(parents=True, exist_ok=True)
        for stale in out.glob("bundle-[0-9][0-9][0-9][0-9][0-9][0-9].json"):
            stale.unlink()
        for n, start in enumerate(range(0, len(resources), size), 1):
            chunk = resources[start : start + size]
            bundle = {
                "resourceType": "Bundle",
                "id": f"bundle-{n:06d}",
                "type": "collection",
                "entry": [
                    {"fullUrl": f"{base}/{r['resourceType']}/{r['id']}", "resource": r}
                    for r in chunk
                ],
            }
            write_atomic(
                out / f"bundle-{n:06d}.json",
                json.dumps(bundle, ensure_ascii=False, indent=1) + "\n",
            )
        return len(resources)
