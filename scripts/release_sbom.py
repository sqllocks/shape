"""CycloneDX SBOMs for the release archives (P8-04, T-25).

Every archive a release publishes (the T-04 platform wheels, the T-29 pure wheel, the core sdist
and each plugin's wheel and sdist) gets its own SBOM, ``<archive>.cdx.json`` (CycloneDX 1.6,
JSON), made from the archive itself:

* the archive is the SBOM's subject (``metadata.component``): its name, version, PyPI package
  URL, SHA-256 and licence expression, read from its own metadata;
* every Python requirement it declares (``Requires-Dist``) is a component, ``required`` when it
  applies to a plain install and ``optional`` when only an extra asks for it;
* an archive that carries the Rust kernel (a platform wheel or the core sdist) lists every crate
  of the ``Cargo.lock`` it was built from (the sdist's own copy; for a wheel, which holds no lock
  file, the source tree's, so run this on the commit the wheels were built from, as the release
  workflow does), with the licence the archive's ``THIRD_PARTY_NOTICES_RUST.md`` gives. The
  lock is a superset of what is linked (it holds the build-time crates too; see
  ``scripts/rust_notices.py``); the SBOM says so in a property rather than guessing.

The SBOMs are deterministic: the serial number derives from the archive's hash, and the timestamp
is ``SOURCE_DATE_EPOCH`` when set (omitted otherwise). ``SHA256SUMS`` lists every archive, in
``sha256sum`` format, so the release attestations and the published files can be checked against
one list.

    python scripts/release_sbom.py build dist --out sbom      # one SBOM per archive + SHA256SUMS
    python scripts/release_sbom.py check dist --sboms sbom    # re-check them against the archives
    python scripts/release_sbom.py check dist --sboms sbom --schema   # ... and the CycloneDX schema
    python scripts/release_sbom.py environment dist --out sbom/environment-all.cdx.json

``environment`` is the resolved view: it installs ``sqllocks-shape[all]`` into a fresh virtual
environment, the first-party distributions from ``dist`` only (never an index) and the
third-party ones from PyPI, and runs ``cyclonedx-py environment`` (the ``cyclonedx-bom``
package) over it. It then checks that every first-party distribution is in it at the release
version. ``--core`` picks the core wheel to install (default: the pure wheel).

    python scripts/release_sbom.py requirements sbom/environment-all.cdx.json --out audit.txt

``requirements`` writes the third-party components of an environment SBOM as ``name==version``
lines, for ``pip-audit --no-deps --disable-pip -r audit.txt`` (first-party distributions are not
on an index yet when the release is built, so they are left out).

``--schema`` validates each file against the CycloneDX 1.6 JSON schema with ``cyclonedx-python-lib``
(the ``cyclonedx-bom`` package, which the release workflow installs); it fails when that library
is missing rather than passing silently.

Exit status: 0 when every SBOM was written (or checked) and every check holds, 1 otherwise.
"""

from __future__ import annotations

import argparse
import email.parser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import uuid
import zipfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SPEC_VERSION = "1.6"
TOOL_NAME = "shape-release-sbom"
KERNEL_CRATE = "shape-kernel"
NOTICES_RUST = "THIRD_PARTY_NOTICES_RUST.md"
SUMS = "SHA256SUMS"
SBOM_SUFFIX = ".cdx.json"
# sqllocks_shape-1.0.0-cp311-abi3-manylinux_2_28_x86_64.whl, sqllocks_shape_kafka-1.0.0.tar.gz
WHEEL_RE = re.compile(
    r"^(?P<stem>[A-Za-z0-9_.]+)-(?P<version>[^-]+)-(?P<tag>[^-]+-[^-]+-[^-]+)\.whl$"
)
SDIST_RE = re.compile(r"^(?P<stem>[A-Za-z0-9_.]+)-(?P<version>[^-]+)\.tar\.gz$")
CRATE_ROW = re.compile(r"^\| `([^`]+)` \| ([^ |]+) \| ([^|]+?) \|", re.MULTILINE)
SERIAL_NAMESPACE = uuid.UUID("6f1c3c56-2f7d-4c0e-9a39-8d2b8e1f5a10")


@dataclass(frozen=True)
class Archive:
    """What an SBOM needs from one release archive, read from the archive."""

    path: Path
    kind: str  # "wheel" or "sdist"
    name: str
    version: str
    tag: str | None
    sha256: str
    metadata: email.message.Message
    cargo_lock: str | None  # the Cargo.lock it was built from, when it carries the kernel
    rust_notices: str | None


def normalize(name: str) -> str:
    """PEP 503 normalized project name (also the PyPI package-URL name)."""
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def archives(dist: Path) -> list[Path]:
    return sorted(p for p in dist.iterdir() if p.name.endswith((".whl", ".tar.gz")))


def read_archive(path: Path) -> Archive:
    """Read the metadata (and, for the kernel, the lock file and notices) out of one archive."""
    if m := WHEEL_RE.match(path.name):
        kind, tag = "wheel", m["tag"]
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            meta_name = next(n for n in names if n.endswith(".dist-info/METADATA"))
            meta = z.read(meta_name).decode("utf-8")
            kernel = any(re.search(r"(^|/)shape/_kernel[^/]*\.(so|pyd|dylib)$", n) for n in names)
            notices = next((n for n in names if n.endswith(f"/licenses/{NOTICES_RUST}")), None)
            rust_notices = z.read(notices).decode("utf-8") if kernel and notices else None
            # a wheel holds no Cargo.lock: the one in the source tree it was built from is it
            lock = (ROOT / "rust" / KERNEL_CRATE / "Cargo.lock") if kernel else None
            cargo_lock = lock.read_text(encoding="utf-8") if lock and lock.is_file() else None
    elif m := SDIST_RE.match(path.name):
        kind, tag = "sdist", None
        with tarfile.open(path) as tar:
            members = {mm.name.split("/", 1)[1]: mm for mm in tar.getmembers() if "/" in mm.name}

            def text(member: str) -> str | None:
                mm = members.get(member)
                f = tar.extractfile(mm) if mm is not None and mm.isfile() else None
                return f.read().decode("utf-8") if f is not None else None

            meta = text("PKG-INFO") or ""
            cargo_lock = text(f"rust/{KERNEL_CRATE}/Cargo.lock")
            rust_notices = text(NOTICES_RUST) if cargo_lock is not None else None
    else:
        raise ValueError(f"{path.name}: not a wheel or sdist file name")
    message = email.parser.Parser().parsestr(meta)
    name, version = message.get("Name"), message.get("Version")
    if not name or not version:
        raise ValueError(f"{path.name}: its metadata has no Name or Version")
    if normalize(m["stem"]) != normalize(name) or m["version"] != version:
        raise ValueError(f"{path.name}: file name does not match its metadata {name} {version}")
    return Archive(path, kind, name, version, tag, sha256(path), message, cargo_lock, rust_notices)


def _requirements(meta: email.message.Message) -> dict[str, dict[str, Any]]:
    """Each declared requirement, by normalized name: the requirement strings and its scope."""
    out: dict[str, dict[str, Any]] = {}
    for req in meta.get_all("Requires-Dist") or []:
        m = re.match(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
        if m is None:
            raise ValueError(f"unreadable requirement {req!r}")
        entry = out.setdefault(normalize(m[1]), {"specs": [], "required": False})
        entry["specs"].append(req.strip())
        if not re.search(r"""extra\s*==\s*["']""", req.partition(";")[2]):
            entry["required"] = True
    return out


def _crates(archive: Archive) -> list[tuple[str, str, str | None]]:
    """(name, version, licence) of each crate in the archive's Cargo.lock."""
    if archive.cargo_lock is None:
        return []
    lock = tomllib.loads(archive.cargo_lock)
    licences = {(n, v): lic.strip() for n, v, lic in CRATE_ROW.findall(archive.rust_notices or "")}
    return sorted(
        (p["name"], p["version"], licences.get((p["name"], p["version"])))
        for p in lock.get("package", [])
        if p["name"] != KERNEL_CRATE
    )


def _purl(archive: Archive) -> str:
    return f"pkg:pypi/{normalize(archive.name)}@{archive.version}?file_name={archive.path.name}"


def make_sbom(archive: Archive) -> dict[str, Any]:
    root_ref = _purl(archive)
    subject: dict[str, Any] = {
        "type": "library",
        "bom-ref": root_ref,
        "name": archive.name,
        "version": archive.version,
        "purl": root_ref,
        "hashes": [{"alg": "SHA-256", "content": archive.sha256}],
        "externalReferences": [{"type": "vcs", "url": "https://github.com/sqllocks/shape"}],
        "properties": [{"name": "shape:archive", "value": archive.kind}],
    }
    if lic := archive.metadata.get("License-Expression"):
        subject["licenses"] = [{"expression": lic}]
    if archive.tag:
        subject["properties"].append({"name": "shape:wheel-tag", "value": archive.tag})
    components: list[dict[str, Any]] = []
    required: list[str] = []
    for name, req in sorted(_requirements(archive.metadata).items()):
        ref = f"pkg:pypi/{name}"
        components.append(
            {
                "type": "library",
                "bom-ref": ref,
                "name": name,
                "purl": ref,
                "scope": "required" if req["required"] else "optional",
                "properties": [{"name": "shape:requirement", "value": s} for s in req["specs"]],
            }
        )
        if req["required"]:
            required.append(ref)
    crates = _crates(archive)
    for name, version, lic in crates:
        ref = f"pkg:cargo/{name}@{version}"
        crate: dict[str, Any] = {
            "type": "library",
            "bom-ref": ref,
            "name": name,
            "version": version,
            "purl": ref,
            "scope": "required",
            "properties": [{"name": "shape:source", "value": f"rust/{KERNEL_CRATE}/Cargo.lock"}],
        }
        if lic:
            crate["licenses"] = [{"expression": lic}]
        components.append(crate)
        required.append(ref)
    if crates:
        subject["properties"].append(
            {
                "name": "shape:rust-crates",
                "value": "every Cargo.lock package (a superset of the linked crates: it includes "
                "build-time crates)",
            }
        )
    metadata: dict[str, Any] = {
        "tools": {"components": [{"type": "application", "name": TOOL_NAME}]},
        "component": subject,
    }
    if epoch := os.environ.get("SOURCE_DATE_EPOCH"):
        stamp = datetime.fromtimestamp(int(epoch), tz=UTC)
        metadata["timestamp"] = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": f"urn:uuid:{uuid.uuid5(SERIAL_NAMESPACE, archive.sha256)}",
        "version": 1,
        "metadata": metadata,
        "components": components,
        "dependencies": [{"ref": root_ref, "dependsOn": required}]
        + [{"ref": c["bom-ref"]} for c in components],
    }


def check_sbom(sbom: dict[str, Any], archive: Archive) -> list[str]:
    """Problems with one SBOM against the archive it describes."""
    where = f"{archive.path.name}{SBOM_SUFFIX}"
    problems: list[str] = []
    if sbom.get("bomFormat") != "CycloneDX" or sbom.get("specVersion") != SPEC_VERSION:
        problems.append(f"{where}: not a CycloneDX {SPEC_VERSION} document")
    subject = sbom.get("metadata", {}).get("component", {})
    if (subject.get("name"), subject.get("version")) != (archive.name, archive.version):
        problems.append(f"{where}: subject is not {archive.name} {archive.version}")
    if {"alg": "SHA-256", "content": archive.sha256} not in subject.get("hashes", []):
        problems.append(f"{where}: the SHA-256 is not the archive's")
    components = sbom.get("components", [])
    refs = [subject.get("bom-ref"), *(c.get("bom-ref") for c in components)]
    if len(set(refs)) != len(refs) or None in refs:
        problems.append(f"{where}: bom-refs are missing or not unique")
    for dep in sbom.get("dependencies", []):
        for ref in [dep.get("ref"), *dep.get("dependsOn", [])]:
            if ref not in refs:
                problems.append(f"{where}: dependency {ref!r} names no component")
    for c in components:
        if not str(c.get("purl", "")).startswith(("pkg:pypi/", "pkg:cargo/")):
            problems.append(f"{where}: component {c.get('name')!r} has no package URL")
    declared = {f"pkg:pypi/{n}" for n in _requirements(archive.metadata)}
    missing = sorted(declared - set(refs))
    if missing:
        problems.append(f"{where}: requirements missing: {missing}")
    crates = _crates(archive)
    listed = {c["bom-ref"] for c in components if str(c.get("purl", "")).startswith("pkg:cargo/")}
    if listed != {f"pkg:cargo/{n}@{v}" for n, v, _ in crates}:
        problems.append(f"{where}: the Rust crates are not those of the archive's Cargo.lock")
    if archive.cargo_lock is not None and archive.rust_notices is None:
        problems.append(f"{archive.path.name}: carries the kernel but not {NOTICES_RUST}")
    unlicensed = [f"{n} {v}" for n, v, lic in crates if not lic]
    if unlicensed:
        problems.append(f"{where}: crates without a licence in {NOTICES_RUST}: {unlicensed}")
    return problems


def schema_problems(path: Path) -> list[str]:
    """Validate one SBOM file against the CycloneDX JSON schema (needs cyclonedx-python-lib)."""
    try:
        from cyclonedx.schema import SchemaVersion
        from cyclonedx.validation.json import JsonStrictValidator
    except ImportError:
        return ["--schema needs cyclonedx-python-lib (pip install cyclonedx-bom)"]
    error = JsonStrictValidator(SchemaVersion.V1_6).validate_str(path.read_text(encoding="utf-8"))
    return [f"{path.name}: {error}"] if error is not None else []


def _dump(sbom: dict[str, Any]) -> str:
    return json.dumps(sbom, indent=2, sort_keys=True) + "\n"


def build(dist: Path, out: Path) -> list[str]:
    files = archives(dist)
    if not files:
        return [f"{dist}: no wheels or sdists"]
    out.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    sums: list[str] = []
    for path in files:
        try:
            archive = read_archive(path)
        except (ValueError, OSError, tarfile.TarError, zipfile.BadZipFile, StopIteration) as exc:
            problems.append(f"{path.name}: {exc or 'no METADATA'}")
            continue
        sbom = make_sbom(archive)
        problems += check_sbom(sbom, archive)
        (out / f"{path.name}{SBOM_SUFFIX}").write_text(_dump(sbom), encoding="utf-8")
        sums.append(f"{archive.sha256}  {path.name}\n")
    (out / SUMS).write_text("".join(sums), encoding="utf-8")
    return problems


def check(dist: Path, sboms: Path, *, schema: bool = False) -> list[str]:
    files = archives(dist)
    if not files:
        return [f"{dist}: no wheels or sdists"]
    problems: list[str] = []
    sums_file = sboms / SUMS
    expected_sums = sums_file.read_text(encoding="utf-8") if sums_file.is_file() else None
    sums: list[str] = []
    for path in files:
        sbom_path = sboms / f"{path.name}{SBOM_SUFFIX}"
        if not sbom_path.is_file():
            problems.append(f"{path.name}: no SBOM ({sbom_path.name})")
            continue
        archive = read_archive(path)
        sbom = json.loads(sbom_path.read_text(encoding="utf-8"))
        problems += check_sbom(sbom, archive)
        if sbom_path.read_text(encoding="utf-8") != _dump(make_sbom(archive)):
            problems.append(f"{sbom_path.name}: differs from the SBOM the archive produces")
        if schema:
            problems += schema_problems(sbom_path)
        sums.append(f"{archive.sha256}  {path.name}\n")
    stale = sorted(
        p.name[: -len(SBOM_SUFFIX)]
        for p in sboms.glob(f"*{SBOM_SUFFIX}")
        if not (dist / p.name[: -len(SBOM_SUFFIX)]).is_file()
    )
    if stale:
        problems.append(f"SBOMs for archives that are not in {dist}: {stale}")
    if expected_sums != "".join(sums):
        problems.append(f"{SUMS} does not list exactly the archives and their SHA-256")
    return problems


def _venv_python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def environment(dist: Path, out: Path, *, core: Path | None = None) -> list[str]:
    """Write the CycloneDX SBOM of a clean ``sqllocks-shape[all]`` install from ``dist``."""
    files = archives(dist)
    wheels = [p for p in files if p.name.endswith(".whl")]
    cores = [p for p in wheels if WHEEL_RE.match(p.name) and p.name.startswith("sqllocks_shape-")]
    pure = [p for p in cores if p.name.endswith("-py3-none-any.whl")]
    core_wheel = core or (pure[0] if len(pure) == 1 else None)
    if core_wheel is None:
        return [f"{dist}: no single pure core wheel; pass --core"]
    version = read_archive(core_wheel).version
    plugins = [p for p in wheels if p.name.startswith("sqllocks_shape_")]
    if shutil.which("cyclonedx-py") is None:
        return ["cyclonedx-py is not installed (pip install cyclonedx-bom)"]
    with tempfile.TemporaryDirectory(prefix="shape-sbom-env-") as tmp:
        venv = Path(tmp) / "venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)], check=True)
        pip = [sys.executable, "-m", "pip", "--python", str(_venv_python(venv)), "install", "-q"]
        # first-party from dist only, so no index can substitute another build of the same version
        subprocess.run(
            [*pip, "--no-index", "--no-deps", str(core_wheel), *map(str, plugins)], check=True
        )
        subprocess.run(
            [*pip, "--find-links", str(dist), f"sqllocks-shape[all]=={version}"], check=True
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "cyclonedx-py",
                "environment",
                str(_venv_python(venv)),
                "--pyproject",
                str(ROOT / "pyproject.toml"),
                "--mc-type",
                "library",
                "--sv",
                SPEC_VERSION,
                "--output-reproducible",
                "--of",
                "JSON",
                "-o",
                str(out),
            ],
            check=True,
        )
    sbom = json.loads(out.read_text(encoding="utf-8"))
    # cyclonedx-py makes the core (named by --pyproject) the subject, not a component
    listed = [sbom.get("metadata", {}).get("component", {}), *sbom.get("components", [])]
    have = {(normalize(c.get("name", "")), c.get("version")) for c in listed}
    want = {(normalize(read_archive(p).name), version) for p in plugins}
    want.add(("sqllocks-shape", version))
    problems = [f"{out.name}: {n} {v} is not in the environment" for n, v in sorted(want - have)]
    for name in ("numpy", "pyarrow"):
        if not any(n == name for n, _ in have):
            problems.append(f"{out.name}: {name} is not in the environment")
    return problems


def audit_requirements(sbom: dict[str, Any]) -> list[str]:
    """``name==version`` of every third-party component of an environment SBOM, sorted."""
    lines = {
        f"{normalize(c['name'])}=={c['version']}"
        for c in sbom.get("components", [])
        if c.get("name")
        and c.get("version")
        and not normalize(c["name"]).startswith("sqllocks-shape")
    }
    return sorted(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="write an SBOM per archive, and SHA256SUMS")
    b.add_argument("dist", type=Path)
    b.add_argument("--out", type=Path, required=True)
    c = sub.add_parser("check", help="check the SBOMs and SHA256SUMS against the archives")
    c.add_argument("dist", type=Path)
    c.add_argument("--sboms", type=Path, required=True)
    c.add_argument("--schema", action="store_true", help="also validate the CycloneDX schema")
    e = sub.add_parser("environment", help="SBOM of a clean sqllocks-shape[all] install")
    e.add_argument("dist", type=Path)
    e.add_argument("--out", type=Path, required=True)
    e.add_argument("--core", type=Path, help="core wheel to install (default: the pure wheel)")
    r = sub.add_parser("requirements", help="third-party pins of an environment SBOM")
    r.add_argument("sbom", type=Path)
    r.add_argument("--out", type=Path, required=True)
    ns = p.parse_args(argv)
    if ns.command == "requirements":
        lines = audit_requirements(json.loads(ns.sbom.read_text(encoding="utf-8")))
        if not lines:
            print(f"FAIL {ns.sbom}: no third-party components", file=sys.stderr)
            return 1
        ns.out.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        print(f"{len(lines)} requirements written to {ns.out}")
        return 0
    if ns.command == "environment":
        problems = environment(ns.dist, ns.out, core=ns.core)
        for line in problems:
            print(f"FAIL {line}", file=sys.stderr)
        if not problems:
            print(f"environment SBOM OK: {ns.out}")
        return 1 if problems else 0
    if ns.command == "build":
        problems = build(ns.dist, ns.out)
        if not problems and (ns.out / SUMS).is_file():
            problems = check(ns.dist, ns.out)
    else:
        problems = check(ns.dist, ns.sboms, schema=ns.schema)
    for line in problems:
        print(f"FAIL {line}", file=sys.stderr)
    if not problems:
        n = len(archives(ns.dist))
        print(
            f"SBOMs OK: {n} archives in {ns.dist}"
            + (" (schema checked)" if getattr(ns, "schema", False) else "")
        )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
