import pytest
from shape_fabric.testing import sample_batches


@pytest.fixture(autouse=True)
def emulator_transport_diagnostics(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep management replies and failed ingestion replies in failed emulator test logs."""
    if request.node.get_closest_marker("emulator") is None:
        return

    import json
    import sys
    import time
    from datetime import UTC, datetime
    from urllib.parse import urlsplit

    from shape_fabric import kusto

    original = kusto.urllib_transport

    def traced(method, url, headers, body, timeout):
        path = urlsplit(url).path
        started = time.monotonic()
        command = body.decode("utf-8", "replace") if path == "/v1/rest/mgmt" else None
        if command is not None:
            print(
                "emulator management started: "
                + json.dumps({"time": datetime.now(UTC).isoformat(), "command": command}),
                file=sys.stderr,
                flush=True,
            )
        try:
            result = original(method, url, headers, body, timeout)
        except Exception as exc:
            record = {
                "time": datetime.now(UTC).isoformat(),
                "path": path,
                "elapsed": time.monotonic() - started,
                "exception_type": type(exc).__name__,
            }
            if command is not None:
                record["command"] = command
            print(
                "emulator transport exception: " + json.dumps(record), file=sys.stderr, flush=True
            )
            raise
        status, _, data = result
        if path == "/v1/rest/mgmt" or (path.startswith("/v1/rest/ingest/") and status >= 300):
            record = {
                "time": datetime.now(UTC).isoformat(),
                "path": path,
                "status": status,
                "response": data.decode("utf-8", "replace"),
                "elapsed": time.monotonic() - started,
            }
            if path == "/v1/rest/mgmt":
                record["command"] = body.decode("utf-8", "replace")
            print("emulator transport: " + json.dumps(record), file=sys.stderr, flush=True)
        return result

    monkeypatch.setattr(kusto, "urllib_transport", traced)


@pytest.fixture
def batches():
    return sample_batches()


def _register_fake_sempy() -> None:
    """``fake_sempy`` (a fake ``sempy.fabric``) importable by name: the suite runs in importlib
    mode, which does not put this folder on ``sys.path``."""
    import importlib.util
    import sys
    from pathlib import Path

    if "fake_sempy" in sys.modules:
        return
    path = Path(__file__).with_name("fake_sempy.py")
    spec = importlib.util.spec_from_file_location("fake_sempy", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["fake_sempy"] = module
    spec.loader.exec_module(module)


_register_fake_sempy()


@pytest.fixture
def windows_text_io(monkeypatch: pytest.MonkeyPatch) -> None:
    """Text files behave as on Windows: the locale encoding is cp1252 and a text-mode write turns
    ``"\\n"`` into ``"\\r\\n"`` unless ``newline=`` says otherwise."""
    import _pyio
    import builtins
    import io
    import os

    monkeypatch.setattr(os, "linesep", "\r\n")
    monkeypatch.setattr(_pyio.TextIOWrapper, "_get_locale_encoding", lambda self: "cp1252")
    monkeypatch.setattr(io, "open", _pyio.open)
    monkeypatch.setattr(builtins, "open", _pyio.open)
