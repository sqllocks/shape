"""Build the pure-Python wheel ``sqllocks_shape-<version>-py3-none-any.whl``.

The wheel holds only Python source (no compiled code), so it installs on any platform. That is
the form Fabric User Data Functions need for a private library (platform independent, under
28.6 MB), and it is the form the Fabric notebook and Environment install too.

The wheel is written directly (a wheel is a zip file plus ``*.dist-info``), so the build needs
nothing but the standard library. Its metadata is generated here rather than taken from
``pyproject.toml``: the demo wheel declares ``numpy>=2.0,<3`` and ``pyarrow>=14`` so that the
libraries preinstalled in Fabric are accepted, and it does not require ``cryptography``,
``pydantic`` or ``typing-extensions`` (``import shape`` does not use them). The stricter pins in
``pyproject.toml`` return with plan work package P0-05.

    python scripts/build_pure_wheel.py                # build into dist/, run the self-checks
    python scripts/build_pure_wheel.py --verify       # also install it in a fresh venv and test
    python scripts/build_pure_wheel.py --out DIR --version 0.9.0.dev1

Exit status is 0 only when the wheel was built and every check passed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import subprocess
import sys
import tempfile
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST_NAME = "sqllocks-shape"
WHEEL_STEM = "sqllocks_shape"
TAG = "py3-none-any"
MAX_WHEEL_BYTES = int(28.6 * 1_000_000)  # Fabric User Data Functions private-library limit
FORBIDDEN_SUFFIXES = {
    ".so",
    ".pyd",
    ".dylib",
    ".dll",
    ".a",
    ".o",
    ".c",
    ".cc",
    ".cpp",
    ".pyx",
    ".rs",
}
DEMO_REQUIRES = ["numpy>=2.0,<3", "pyarrow>=14"]
# packages the demo wheel is tested against in a clean environment (DM-03)
VERIFY_PACKAGES = ["numpy", "pyarrow", "pandas", "deltalake", "pytest"]
FIXED_TIME = (2026, 1, 1, 0, 0, 0)  # reproducible archives


def _read_pyproject() -> dict:
    with open(ROOT / "pyproject.toml", "rb") as fh:
        return tomllib.load(fh)


def _source_files() -> list[tuple[str, Path]]:
    """(path inside the wheel, source file) for every file of the ``shape`` package."""
    files: list[tuple[str, Path]] = []
    pkg = ROOT / "src" / "shape"
    for path in sorted(pkg.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix == ".pyc":
            continue
        files.append((path.relative_to(ROOT / "src").as_posix(), path))
    specs = ROOT / "docs" / "specs"
    if specs.is_dir():  # pyproject: force-include "docs/specs" -> "shape/schemas"
        for path in sorted(specs.rglob("*")):
            if path.is_file():
                files.append(("shape/schemas/" + path.relative_to(specs).as_posix(), path))
    return files


def _metadata(project: dict, version: str) -> str:
    lines = [
        "Metadata-Version: 2.1",
        f"Name: {DIST_NAME}",
        f"Version: {version}",
        f"Summary: {project.get('description', '')}",
        f"Requires-Python: {project.get('requires-python', '>=3.11')}",
        "License: Apache-2.0",
    ]
    for label, url in project.get("urls", {}).items():
        lines.append(f"Project-URL: {label}, {url}")
    lines += [f"Requires-Dist: {req}" for req in DEMO_REQUIRES]
    for extra, reqs in project.get("optional-dependencies", {}).items():
        if extra in {"delta", "pandas", "yaml"}:
            lines.append(f"Provides-Extra: {extra}")
            lines += [f"Requires-Dist: {req}; extra == '{extra}'" for req in reqs]
    lines.append("Classifier: Programming Language :: Python :: 3")
    lines.append("Classifier: License :: OSI Approved :: Apache Software License")
    lines.append("Classifier: Operating System :: OS Independent")
    readme = ROOT / "README.md"
    body = ""
    if readme.is_file():
        lines.append("Description-Content-Type: text/markdown")
        body = readme.read_text(encoding="utf-8")
    return "\n".join(lines) + "\n\n" + body


def _record_hash(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def build(out_dir: Path, version: str | None = None) -> Path:
    project = _read_pyproject()["project"]
    version = version or project["version"]
    dist_info = f"{WHEEL_STEM}-{version}.dist-info"
    entries: list[tuple[str, bytes]] = []
    for arc, src in _source_files():
        entries.append((arc, src.read_bytes()))
    licence = ROOT / "LICENSE"
    if licence.is_file():
        entries.append((f"{dist_info}/licenses/LICENSE", licence.read_bytes()))
    entries.append((f"{dist_info}/METADATA", _metadata(project, version).encode("utf-8")))
    wheel_meta = (
        "Wheel-Version: 1.0\nGenerator: sqllocks-shape build_pure_wheel.py\n"
        f"Root-Is-Purelib: true\nTag: {TAG}\n"
    )
    entries.append((f"{dist_info}/WHEEL", wheel_meta.encode("utf-8")))
    scripts = project.get("scripts", {})
    if scripts:
        console = "[console_scripts]\n" + "".join(f"{k} = {v}\n" for k, v in scripts.items())
        entries.append((f"{dist_info}/entry_points.txt", console.encode("utf-8")))
    entries.append((f"{dist_info}/top_level.txt", b"shape\n"))

    names = [name for name, _ in entries]
    if len(names) != len(set(names)):
        raise SystemExit("duplicate file names in the wheel")
    bad = [n for n in names if Path(n).suffix.lower() in FORBIDDEN_SUFFIXES]
    if bad:
        raise SystemExit(f"compiled or native files are not allowed in a pure wheel: {bad[:5]}")

    record_lines = [f"{name},{_record_hash(data)},{len(data)}" for name, data in entries]
    record_lines.append(f"{dist_info}/RECORD,,")
    entries.append((f"{dist_info}/RECORD", ("\n".join(record_lines) + "\n").encode("utf-8")))

    out_dir.mkdir(parents=True, exist_ok=True)
    wheel = out_dir / f"{WHEEL_STEM}-{version}-{TAG}.whl"
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for name, data in entries:
            info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, data)
    return wheel


def check(wheel: Path) -> list[str]:
    """Return a list of problems (empty when the wheel is acceptable)."""
    problems: list[str] = []
    if not wheel.name.endswith(f"-{TAG}.whl"):
        problems.append(f"wheel tag is not {TAG}: {wheel.name}")
    size = wheel.stat().st_size
    if size >= MAX_WHEEL_BYTES:
        problems.append(f"wheel is {size} bytes, limit is {MAX_WHEEL_BYTES}")
    with zipfile.ZipFile(wheel) as z:
        bad_member = z.testzip()
        if bad_member:
            problems.append(f"corrupt archive member: {bad_member}")
        names = z.namelist()
        native = [n for n in names if Path(n).suffix.lower() in FORBIDDEN_SUFFIXES]
        if native:
            problems.append(f"compiled code in wheel: {native[:5]}")
        info = next((n for n in names if n.endswith(".dist-info/WHEEL")), None)
        if info is None:
            problems.append("WHEEL file missing")
        else:
            text = z.read(info).decode()
            if f"Tag: {TAG}" not in text or "Root-Is-Purelib: true" not in text:
                problems.append("WHEEL file does not declare a pure py3-none-any wheel")
        meta_name = next((n for n in names if n.endswith(".dist-info/METADATA")), None)
        if meta_name is None:
            problems.append("METADATA missing")
        else:
            meta = z.read(meta_name).decode().split("\n\n", 1)[0]
            for req in DEMO_REQUIRES:
                if f"Requires-Dist: {req}" not in meta:
                    problems.append(f"METADATA does not declare {req}")
            for banned in ("cryptography", "pydantic"):
                if f"Requires-Dist: {banned}" in meta:
                    problems.append(f"METADATA must not require {banned}")
        record = next((n for n in names if n.endswith(".dist-info/RECORD")), None)
        if record is None:
            problems.append("RECORD missing")
        else:
            listed = {}
            for line in z.read(record).decode().splitlines():
                path, digest, _size = line.rsplit(",", 2)
                listed[path] = digest
            for n in names:
                if n == record:
                    continue
                if listed.get(n) != _record_hash(z.read(n)):
                    problems.append(f"RECORD hash mismatch for {n}")
            for n in listed:
                if n not in names:
                    problems.append(f"RECORD lists a missing file: {n}")
    return problems


def _run(cmd: list[str], **kwargs: object) -> None:
    print("+", " ".join(str(c) for c in cmd), flush=True)
    subprocess.run(cmd, check=True, **kwargs)  # type: ignore[call-overload]


def verify(wheel: Path, python: str) -> None:
    """Install the wheel into a fresh venv holding only numpy, pyarrow, pandas and deltalake,
    import it, and run the ``tests/demo/core`` suite against the installed copy."""
    with tempfile.TemporaryDirectory(prefix="shape-wheel-verify-") as tmp:
        venv = Path(tmp) / "venv"
        _run([python, "-m", "venv", str(venv)])
        bindir = venv / ("Scripts" if sys.platform == "win32" else "bin")
        py = str(bindir / ("python.exe" if sys.platform == "win32" else "python"))
        _run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        _run([py, "-m", "pip", "install", "--quiet", *VERIFY_PACKAGES])
        _run([py, "-m", "pip", "install", "--quiet", str(wheel)])
        # run from a scratch directory so the source tree cannot shadow the installed wheel
        probe = (
            "import importlib.util, sys; import shape; "
            "print('shape', shape.__version__, shape.__file__); "
            "assert 'site-packages' in shape.__file__, 'not the installed wheel'; "
            "assert importlib.util.find_spec('cryptography') is None, 'cryptography present'; "
            "assert 'cryptography' not in sys.modules"
        )
        _run([py, "-c", probe], cwd=tmp)
        _run(
            [
                py,
                "-m",
                "pytest",
                "-q",
                "-p",
                "no:cacheprovider",
                "--rootdir",
                str(ROOT),
                str(ROOT / "tests" / "demo" / "core"),
            ],
            cwd=tmp,
        )
        _run([str(bindir / "shape"), "--help"], cwd=tmp, stdout=subprocess.DEVNULL)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--out", type=Path, default=ROOT / "dist")
    parser.add_argument("--version", default=None, help="override the pyproject version")
    parser.add_argument(
        "--verify", action="store_true", help="install into a fresh venv and run tests/demo/core"
    )
    parser.add_argument("--python", default=sys.executable, help="interpreter for --verify")
    args = parser.parse_args(argv)

    wheel = build(args.out, args.version)
    problems = check(wheel)
    print(f"{wheel.name}  {wheel.stat().st_size:,} bytes")
    if problems:
        for p in problems:
            print("FAIL:", p, file=sys.stderr)
        return 1
    print(f"checks passed: tag {TAG}, < {MAX_WHEEL_BYTES:,} bytes, no compiled code, RECORD valid")
    if args.verify:
        verify(wheel, args.python)
        print("verify passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
