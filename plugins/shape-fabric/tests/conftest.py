import pytest
from shape_fabric.testing import sample_batches


@pytest.fixture
def batches():
    return sample_batches()


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
