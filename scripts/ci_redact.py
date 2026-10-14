"""Redact the identifiers the seed scripts of the public ``fabric-live`` workflow print.

This repository is public, so its Actions logs are public. ``fabric_seed_retail.py`` and
``fabric_update_shape_sm.py`` pass everything they print through ``redact``: it masks every GUID
and SQL endpoint host it finds (``::add-mask::``) and replaces the masked variables' values first
(longest first), then every host, then every GUID. Redact the whole text before truncating it, so
a cut never leaves part of a GUID. The workflow's own steps keep their inline copy: they run from
the workflow's commit, while ``scripts/`` comes from the ref under test (the ``ref`` input).
"""

from __future__ import annotations

import os
import re

GUID = re.compile(r"[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}")
# SQL analytics endpoint, Warehouse, SQL database, Eventhouse and Synapse hosts: the first label
# identifies the item and is not a GUID.
HOST = re.compile(
    r"[A-Za-z0-9-]+\.(?:(?:datawarehouse|database|kusto)\.fabric\.microsoft\.com"
    r"|sql\.azuresynapse\.net)"
)
MASKED_VARS = (
    "FABRIC_TENANT_ID",
    "FABRIC_CLIENT_ID",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_GIT_WORKSPACE_ID",
    "FABRIC_LAKEHOUSE_ID",
    "FABRIC_SQL_ENDPOINT",
    "FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH",
    "FABRIC_SCHEMA_LAKEHOUSE_TABLES_PATH",
    "FABRIC_EVENTHOUSE_URI",
    "FABRIC_STAGING_PATH",
    "FABRIC_SAMPLE_WAREHOUSE_CONNECTION_STRING",
    "FABRIC_WAREHOUSE_CONNECTION_STRING",
    "SHAPE_TEST_SYNAPSE_URI",
    "SHAPE_TEST_SYNAPSE_STAGING",
    "SYNAPSE_SUBSCRIPTION_ID",
    "SYNAPSE_WORKSPACE",
)
_hidden: set[str] = set()


def _mask(value: str) -> None:
    for line in value.splitlines():
        if line.strip():
            print(f"::add-mask::{line}")
    for token in sorted(set(GUID.findall(value)) | set(HOST.findall(value))):
        print(f"::add-mask::{token}")


def hide(value: str) -> None:
    """Mask a value found at run time (not in ``MASKED_VARS``) and redact it from now on."""
    if value:
        _hidden.add(value)
        _mask(value)


def _values() -> list[str]:
    found = {os.environ[k] for k in MASKED_VARS if os.environ.get(k)} | _hidden
    return sorted(found, key=len, reverse=True)


def redact(text: object) -> str:
    """Mask every GUID and host in the WHOLE text and replace them; truncate only afterwards."""
    text = str(text)
    for token in sorted(set(GUID.findall(text)) | set(HOST.findall(text))):
        print(f"::add-mask::{token}")
    for value in _values():
        text = text.replace(value, "<redacted>")
    return GUID.sub("<guid>", HOST.sub("<host>", text))
