"""The process-wide allocator setting (``shape._process``): one place, same for the command line and
the Python API, switchable off, and never a change of value. Each case runs in a fresh interpreter,
because what matters is the order of the imports."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

PROBE = "import pyarrow as pa; print(pa.default_memory_pool().backend_name)"


def _run(code: str, **env: str) -> str:
    clean = {
        k: v
        for k, v in os.environ.items()
        if k not in ("ARROW_DEFAULT_MEMORY_POOL", "SHAPE_MEMORY_POOL")
    }
    done = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env={**clean, **env},
        timeout=120,
        check=True,
    )
    return done.stdout.strip()


def test_import_shape_first_selects_the_system_pool():
    # what `shape generate` does: the package is imported before Arrow
    assert _run("import shape\n" + PROBE) == "system"


def test_the_command_line_module_selects_the_system_pool():
    assert _run("import shape.cli.main\n" + PROBE) == "system"


def test_shape_memory_pool_default_turns_it_off():
    default = _run(PROBE)
    assert _run("import shape\n" + PROBE, SHAPE_MEMORY_POOL="default") == default


def test_an_explicit_arrow_choice_is_kept():
    names = _run("import pyarrow as pa; print(','.join(pa.supported_memory_backends()))")
    assert "system" in names
    assert _run("import shape\n" + PROBE, ARROW_DEFAULT_MEMORY_POOL="system") == "system"
    other = next((b for b in names.split(",") if b != "system"), None)
    if other is not None:
        assert _run("import shape\n" + PROBE, ARROW_DEFAULT_MEMORY_POOL=other) == other


def test_configure_reports_what_it_did():
    # shape was imported (and configured) first, so Arrow is not loaded yet: the choice is "kept"
    assert _run("import shape, shape._process as p; print(p.configure())") == "kept"
    assert (
        _run("import shape, shape._process as p; print(p.configure())", SHAPE_MEMORY_POOL="default")
        == "off"
    )


@pytest.mark.skipif(
    not sys.platform.startswith("linux") or not os.path.exists("/proc/self/status"),
    reason="prctl(PR_SET_THP_DISABLE) is Linux",
)
def test_arrow_imported_first_switches_huge_pages_off():
    out = _run(
        "import pyarrow as pa\n"
        "first = pa.default_memory_pool().backend_name\n"
        "import shape._process as p\n"
        "did = p.configure()\n"
        "lines = open('/proc/self/status').read().splitlines()\n"
        "state = [x.split()[-1] for x in lines if x.startswith('THP_enabled')]\n"
        "print(first, did, state)"
    )
    first, did, state = out.split(" ", 2)
    if first == "system":
        assert did == "none"
    else:
        assert did == "thp"
        assert state == "['0']"  # the kernel reports the process as THP-disabled


def test_generation_values_do_not_depend_on_the_allocator():
    code = (
        "import shape, hashlib\n"
        "r = shape.generate('retail', scale='small', seed=7)\n"
        "h = hashlib.sha256()\n"
        "for name in sorted(r.tables):\n"
        "    t = r.tables[name]\n"
        "    for c in t.column_names:\n"
        "        h.update(str(t[c].to_pylist()[:2000]).encode())\n"
        "print(h.hexdigest())"
    )
    assert _run(code) == _run(code, SHAPE_MEMORY_POOL="default")
