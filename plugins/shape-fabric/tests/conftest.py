import pytest
from shape_fabric.testing import sample_batches


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
