from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("shape_domains")
pytest.importorskip("yaml")

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "packs"


@pytest.fixture(scope="session")
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(scope="session")
def retail():
    from shape.generation.domains import load_domain

    return load_domain("retail")


def write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


PACK = """\
pack_version: 1
id: t
kind: file_drop
domain: retail
description: test
fabric_targets:
  lakehouse_files_root: Files/landing
file_drop:
  formats: [{fmt}]
  entities: [customer, order]
validation:
  required_gates: [schema_conformance]
"""

STREAM = """\
pack_version: 1
id: s
kind: stream
domain: retail
description: test
fabric_targets: {x: 1}
streaming:
  cadence: {rate_per_sec: 5}
  topics:
    - {name: order, event_type: order_placed, payload_fields: [order_id]}
    - {name: nothing_like_it, event_type: e, payload_fields: [a]}
validation:
  required_gates: [row_count]
"""

HYBRID = """\
pack_version: 1
id: h
kind: hybrid
domain: retail
description: test
fabric_targets: {x: 1}
hybrid:
  micro_batch:
    formats: [jsonl]
    entities: [customer]
  stream:
    topics:
      - {name: customer, event_type: updated, payload_fields: [customer_id]}
validation:
  required_gates: [referential_integrity, uniqueness]
"""
