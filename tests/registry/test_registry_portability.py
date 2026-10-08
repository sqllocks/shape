"""The registry writes the same bytes on every platform (AUD-portability).

``windows_text_io`` makes text files behave as they do on Windows: the locale encoding is cp1252
and a text-mode write turns ``"\\n"`` into ``"\\r\\n"`` unless ``newline=`` says otherwise."""

from __future__ import annotations

import _pyio
import builtins
import io
import os

import pytest

from shape.registry import LocalRegistry


@pytest.fixture
def windows_text_io(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(os, "linesep", "\r\n")
    monkeypatch.setattr(_pyio.TextIOWrapper, "_get_locale_encoding", lambda self: "cp1252")
    monkeypatch.setattr(io, "open", _pyio.open)
    monkeypatch.setattr(builtins, "open", _pyio.open)


def test_local_registry_files_are_byte_identical_on_windows(tmp_path, windows_text_io):
    r = LocalRegistry(tmp_path)
    h = r.commit("customer", b"one", {"owner": "Zoë"})
    r.tag("customer", "v1")
    log = (tmp_path / "logs" / "customer.jsonl").read_bytes()
    assert log.endswith(b"}\n") and b"\r" not in log
    assert (tmp_path / "refs" / "customer" / "latest").read_bytes() == h.encode("ascii")
    assert (tmp_path / "tags" / "customer" / "v1").read_bytes() == h.encode("ascii")
    assert r.log("customer")[0]["metadata"] == {"owner": "Zoë"}
    assert r.resolve("customer", "v1") == h


def test_profile_registry_index_paths_use_forward_slashes(tmp_path):
    """``_index.json`` is the same on every platform: its ``path`` is ``/``-separated (on Windows
    ``str(Path.relative_to(...))`` would join with ``\\``)."""
    import json

    import pyarrow as pa

    import shape
    from shape.registry.profiles import INDEX, ProfileRegistry

    prof = shape.profile(pa.table({"id": [1, 2, 3]}), name="orders")
    reg = ProfileRegistry(tmp_path)
    reg.save(prof, system="crm", name="v1")
    index = json.loads((tmp_path / INDEX).read_text(encoding="utf-8"))
    assert {e["path"] for e in index.values()} == {"crm/orders/v1.shape"}
