"""Run the official HL7 FHIR validator over the generated Bundles.

Set ``SHAPE_FHIR_VALIDATOR_JAR`` to a ``validator_cli.jar`` (see VALIDATION.md) and have Java
on the path. Without them the test is skipped, since it cannot run. The validator needs the
network to fetch the US Core and CARIN BB packages and, unless ``SHAPE_FHIR_TX=n/a`` is set,
to reach a terminology server.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from shape_healthcare_standards.common import TableSet
from shape_healthcare_standards.fhir.resources import payer_organizations
from shape_healthcare_standards.fhir.sink import FhirBundleSink
from shape_healthcare_standards.testing import sample_tables

pytestmark = pytest.mark.live

TABLES = ("member", "eligibility", "provider", "medical_claim", "pharmacy_claim")
IGS = ("hl7.fhir.us.core#6.1.0", "hl7.fhir.us.carin-bb#2.1.0")

# The sample's first drug uses a synthetic NDC that is not in the NDC directory.
SYNTHETIC_NDC = re.compile(
    r"Unknown code '(00093726001)' in the CodeSystem 'http://hl7\.org/fhir/sid/ndc'"
)


def _issues(report: dict[str, Any]) -> list[tuple[str, str, str]]:
    out = []
    for entry in report["entry"]:
        outcome = entry["resource"]
        name = outcome["extension"][0]["valueString"]
        for issue in outcome["issue"]:
            out.append((name, issue["severity"], issue["details"]["text"]))
    return out


def test_generated_bundles_pass_the_official_validator(tmp_path: Path) -> None:
    jar = os.environ.get("SHAPE_FHIR_VALIDATOR_JAR", "")
    if not jar or not Path(jar).is_file():
        pytest.skip("SHAPE_FHIR_VALIDATOR_JAR does not point to a validator_cli.jar")
    if shutil.which("java") is None:
        pytest.skip("java is not on the path")

    tables = sample_tables()
    dirs = []
    for table in TABLES:
        out = tmp_path / table
        FhirBundleSink().write(str(out), table, tables[table].to_batches(), tables=tables)
        dirs.append(out)
    payers = tmp_path / "payer"
    payers.mkdir()
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "entry": [
            {"fullUrl": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, r['id'])}", "resource": r}
            for r in payer_organizations(TableSet(tables))
        ],
    }
    (payers / "payers.json").write_text(json.dumps(bundle), encoding="utf-8")
    dirs.append(payers)

    report_path = tmp_path / "report.json"
    cmd = ["java", "-jar", jar, *map(str, dirs), "-version", "4.0.1"]
    for ig in IGS:
        cmd += ["-ig", ig]
    tx = os.environ.get("SHAPE_FHIR_TX")
    if tx:
        cmd += ["-tx", tx]
    cmd += ["-output", str(report_path)]
    run = subprocess.run(cmd, capture_output=True, text=True, timeout=900, check=False)
    assert report_path.is_file(), f"validator wrote no report:\n{run.stdout[-2000:]}"
    issues = _issues(json.loads(report_path.read_text(encoding="utf-8")))

    files = {name for name, _, _ in issues}
    assert len(files) >= len(dirs), "the validator did not report every bundle"
    errors = [
        (name, text)
        for name, severity, text in issues
        if severity in ("error", "fatal") and not SYNTHETIC_NDC.search(text)
    ]
    assert errors == []
