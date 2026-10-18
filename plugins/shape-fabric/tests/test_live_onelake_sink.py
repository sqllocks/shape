"""The core ``abfss://`` sink writes into a Lakehouse's Files area (docs/SINKS.md example 0).

``shape generate --to abfss://<workspace>@onelake.dfs.fabric.microsoft.com/<item>/Files/<folder>``
uploads each file to ``_shape_tmp/`` and publishes it under its final name. A missing variable
fails the test with its name; a service failure fails with the service's own message.
"""

import os
import uuid

import pytest
from shape_fabric import onelake
from shape_fabric._storage import Storage

from shape.cli.main import main

pytestmark = pytest.mark.live


def need(name):
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"live test needs {name}")
    return value


def test_the_abfss_sink_publishes_files_into_a_lakehouse_files_folder(capsys):
    from azure.identity import ClientSecretCredential

    tables = need("FABRIC_CLASSIC_LAKEHOUSE_TABLES_PATH").rstrip("/")
    assert tables.endswith("/Tables")
    root = onelake.parse(
        tables.removesuffix("/Tables") + "/Files/shape_live_sink_" + uuid.uuid4().hex[:8]
    )
    credential = ClientSecretCredential(
        need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID"), need("FABRIC_CLIENT_SECRET")
    )
    storage = Storage(credential=credential)
    try:
        code = main(
            [
                "--debug",
                "generate",
                "retail",
                "--scale",
                "small",
                "--seed",
                "7",
                "--to",
                root.abfss(),
                "--table-format",
                "store=csv",
                "--batch-date",
                "2026-10-02",
                "--yes",
            ]
        )
        captured = capsys.readouterr()
        assert code == 0, captured.out + captured.err
        assert storage.exists(root.abfss() + "/order"), "no order folder was published"
        assert storage.exists(root.abfss() + "/store"), "no store folder was published"
    finally:
        storage.remove(root.abfss(), recursive=True)
        credential.close()
