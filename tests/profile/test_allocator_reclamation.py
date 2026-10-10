"""Freed system-pool buffers must be reclaimed on Darwin as well as glibc."""

from types import SimpleNamespace

import pyarrow as pa
import pytest

from shape.profile import _memory


def test_bounded_scan_reclaims_freed_system_pages_without_changing_the_profile(monkeypatch):
    from shape.profile import engine

    source = pa.table({"id": list(range(2048))})
    expected = engine.profile(source, name="t", mode="bounded", batch_size=64)
    events = []

    class Pool:
        backend_name = "system"

        def release_unused(self):
            events.append("arrow")

    def relief(zone, goal):
        assert source.column("id")[2047].as_py() == 2047
        events.append((zone, goal))
        return 0

    monkeypatch.setattr(engine.pa, "default_memory_pool", Pool)
    monkeypatch.setattr(_memory, "_system_pressure_relief", lambda: relief)
    assert engine.profile(source, name="t", mode="bounded", batch_size=64) == expected
    assert events == ["arrow", (None, 0)] * 32


@pytest.mark.parametrize("backend", ["system", "mimalloc", "jemalloc"])
def test_reclamation_preserves_live_buffers_and_only_supplements_the_system_pool(
    monkeypatch, backend
):
    events = []
    live = pa.py_buffer(b"still allocated")

    class Pool:
        backend_name = backend

        def release_unused(self):
            events.append("arrow")

    def relief(zone, goal):
        assert live.to_pybytes() == b"still allocated"
        events.append((zone, goal))
        return 0

    monkeypatch.setattr(_memory, "_system_pressure_relief", lambda: relief)
    _memory.release_unused(Pool())
    assert events == (["arrow", (None, 0)] if backend == "system" else ["arrow"])
    assert live.to_pybytes() == b"still allocated"


def test_darwin_pressure_relief_uses_pointer_and_size_t_arguments(monkeypatch):
    import ctypes

    class Relief:
        pass

    relief = Relief()
    _memory._system_pressure_relief.cache_clear()
    monkeypatch.setattr(_memory, "sys", SimpleNamespace(platform="darwin"))
    monkeypatch.setattr(
        ctypes, "CDLL", lambda handle: SimpleNamespace(malloc_zone_pressure_relief=relief)
    )
    try:
        assert _memory._system_pressure_relief() is relief
        assert relief.argtypes == [ctypes.c_void_p, ctypes.c_size_t]
        assert relief.restype is ctypes.c_size_t
    finally:
        _memory._system_pressure_relief.cache_clear()


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_platform_without_pressure_relief_preserves_arrow_reclamation(monkeypatch, platform):
    import ctypes

    events = []

    class Pool:
        backend_name = "system"

        def release_unused(self):
            events.append("arrow")

    def unavailable(handle):
        raise AttributeError("not supported")

    _memory._system_pressure_relief.cache_clear()
    monkeypatch.setattr(_memory, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(ctypes, "CDLL", unavailable)
    try:
        _memory.release_unused(Pool())
        assert events == ["arrow"]
    finally:
        _memory._system_pressure_relief.cache_clear()
