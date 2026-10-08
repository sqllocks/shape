"""#310: the fabric plugin without its ``[eventhubs]`` and ``[sqlserver]`` extras.

Run in a subprocess where the Event Hubs and SQL Server plugins (and ``pyodbc`` and
``azure-eventhub``) cannot be imported, as in a venv that has only the core and this plugin: the
plugin's entry points load, the Lakehouse writer writes, and the pieces that need an extra say
which one to install. The packaging half (the wheel's ``Requires-Dist``) is in the core's
``tests/plugins/test_optional_plugin_pieces.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

#: run in the subprocess before anything else: these modules behave as if not installed
_ABSENT = """
import importlib.abc, sys
_NAMES = tuple(sys.argv.pop(1).split(","))
class _Absent(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if any(name == n or name.startswith(n + ".") for n in _NAMES):
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return None
sys.meta_path.insert(0, _Absent())
"""

_HUBS_AND_SQL = "shape_eventhubs,shape_sqlserver,pyodbc,azure.eventhub"


def _run_without(absent: str, code: str, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("SHAPE_")}
    env["SHAPE_HOME"] = str(cwd / "home")
    return subprocess.run(
        [sys.executable, "-c", _ABSENT + code, absent, *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=600,
    )


_FABRIC_WITHOUT_EXTRAS = r"""
import json, sys
import pyarrow as pa

import shape_fabric
import shape_fabric.commands, shape_fabric.notebook, shape_fabric.sinks, shape_fabric.targets
import shape_fabric.testing
from shape.plugins.host import PluginHost

out = {}
host = PluginHost()
out["broken"] = [
    f"{r.group}:{r.name}: {r.error}" for r in host.load_all()
    if r.target.startswith("shape_fabric") and r.status != "ok"
]

batch = pa.record_batch({"id": [1, 2, 3]})
result = shape_fabric.LakehouseWriter(sys.argv[1]).write_tables({"customer": [batch]})
out["lakehouse_rows"] = result.rows_written

def message(fn):
    try:
        fn()
    except ImportError as exc:
        return str(exc)
    return None

out["eventstream"] = message(
    lambda: shape_fabric.EventstreamEmitter().emit(
        "eventstream://x", [batch], connection_string="Endpoint=sb://x/;EntityPath=e"
    )
)
out["sql"] = message(
    lambda: shape_fabric.SqlDatabaseWriter("Server=tcp:x;Database=y").write_tables(
        {"customer": [batch]}
    )
)
out["sqlserver_sink"] = message(
    lambda: shape_fabric.SqlServerSink().write("mssql://x/y", "customer", [batch])
)
out["harness"] = message(lambda: shape_fabric.testing.EventstreamHarness())
print(json.dumps(out))
"""


def test_the_fabric_plugin_works_without_eventhubs_and_sqlserver(tmp_path: Path) -> None:
    landing = tmp_path / "landing"
    r = _run_without(_HUBS_AND_SQL, _FABRIC_WITHOUT_EXTRAS, tmp_path, str(landing))
    assert r.returncode == 0, r.stdout + r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["broken"] == []
    assert out["lakehouse_rows"] == 3
    assert list((landing / "customer").iterdir())
    for key in ("eventstream", "harness"):
        assert out[key] is not None and "sqllocks-shape-fabric[eventhubs]" in out[key], out
    for key in ("sql", "sqlserver_sink"):
        assert out[key] is not None and "sqllocks-shape-fabric[sqlserver]" in out[key], out


def test_the_fabric_plugin_with_both_extras_is_unchanged() -> None:
    from shape_eventhubs.emitter import EventHubsEmitter
    from shape_fabric import _tsql
    from shape_fabric.eventstream import EventstreamEmitter

    assert issubclass(EventstreamEmitter, EventHubsEmitter)
    assert _tsql.ident("a]b") == "[a]]b]"
    assert "PWD=***" in _tsql.redact("Server=x;PWD=secret")
