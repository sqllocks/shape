"""P7-04 security review regression test for the Fabric plugin (moved here from
tests/security/test_p7_04_review.py so it runs with the plugin installed instead of being skipped
by the core suite). It failed before its fix."""

from __future__ import annotations

import json
import re

import pyarrow as pa
import pytest

pytestmark = pytest.mark.security


def test_kql_mapping_literal_escapes_backslashes():
    from shape_fabric.eventhouse import create_mapping_command

    name = 'x", "path": "$[\\"_shape_seq\\"]", "datatype": "string"}, {"column": "zz'
    cmd = create_mapping_command("t", pa.schema([pa.field(name, pa.int64())]))
    literal = cmd.split("ingestion json mapping 'shape_json' '", 1)[1][:-1]
    # Decode the KQL single-quoted literal the way the service does, then parse the JSON.
    decoded = re.sub(r"\\(.)", r"\1", literal)
    cols = json.loads(decoded)
    assert len(cols) == 1 and cols[0]["column"] == name
