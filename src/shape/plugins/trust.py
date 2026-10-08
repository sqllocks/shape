"""Plugin allow-list and opt-in signature check (W1-18).

Plugins are trusted, in-process code (D-09). This module adds two checks the host runs *before*
it imports a plugin, both opt-in:

* an **allow-list** (``shape-plugin-allowlist`` file): which distributions, versions and plugin
  names may load, and optionally the sha256 of each distribution's ``RECORD`` so that a file
  edited after installation is noticed;
* a **signature** (``shape-plugin.sig`` in the distribution's ``.dist-info``): an Ed25519
  signature, by a key the allow-list trusts, over the distribution's file list.

Neither is a sandbox. They say which code may load and whether its files changed; what loaded
code then does is not restricted (``docs/plugins/trust-model.md``).

Nothing here imports a plugin. Reading metadata and hashing files is all it does, and the
``cryptography`` package (the ``[sign]`` extra) is imported only when a signature is checked.
"""

from __future__ import annotations

import base64
import binascii
import csv
import errno
import hashlib
import io
import json
import os
import re
import stat
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any

from shape.errors import ShapeError

FORMAT = "shape-plugin-allowlist"
VERSION = 1
ENV_ALLOWLIST = "SHAPE_PLUGIN_ALLOWLIST"
SIGNATURE_FILE = "shape-plugin.sig"
SIGNATURE_FORMAT = "shape-plugin-signature"
SIGNATURE_VERSION = 1
ALGORITHM = "Ed25519"
CORE_DISTRIBUTION = "sqllocks-shape"
SIGN_HINT = "pip install 'sqllocks-shape[sign]'"

# Domain separation: a signature over a plugin file list is never valid for any other message.
_DOMAIN = b"shape-plugin-signature-v1\n"
_MAX_ALLOWLIST_BYTES = 1024 * 1024
_KEY_BYTES = 32
_TOP_KEYS = {"format", "version", "plugins", "require_signature", "trusted_keys"}
_ENTRY_KEYS = {"distribution", "version", "record_sha256", "names"}
_KEY_KEYS = {"key_id", "public_key"}
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
# Files an installer writes that the wheel's own RECORD does not list: never part of the signed
# file list, so the same signature holds for the wheel and for what pip installs from it.
_INSTALLER_FILES = {"INSTALLER", "REQUESTED", "direct_url.json"}

#: ``blocked_kind`` values: the plugin is simply not permitted, or a permitted one failed a check.
NOT_PERMITTED = "not_permitted"
CHECK_FAILED = "check_failed"


class AllowlistError(ShapeError):
    """The allow-list file cannot be used."""


class PluginTrustError(ShapeError):
    """A distribution's files or signature do not pass; the message says why."""


class MissingCryptoError(PluginTrustError):
    """Signatures need the ``cryptography`` package."""


def canonical_name(name: str) -> str:
    """The PEP 503 form of a distribution name."""
    return re.sub(r"[-_.]+", "-", name).lower()


def short_group(group: str) -> str:
    return group[len("shape.") :] if group.startswith("shape.") else group


def crypto_available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def key_id(public_key: bytes) -> str:
    """Short fingerprint of a public key: the same id ``shape keygen`` and artifact signing use."""
    return hashlib.sha256(public_key).hexdigest()[:16]


# -- the allow-list file ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AllowedPlugin:
    distribution: str
    version: str | None = None
    record_sha256: str | None = None
    names: tuple[tuple[str, str], ...] | None = None  # (group, name); ``None`` means any


@dataclass(frozen=True, slots=True)
class Allowlist:
    path: str
    plugins: Mapping[str, AllowedPlugin]  # by canonical distribution name
    require_signature: bool = False
    trusted_keys: Mapping[str, bytes] = field(default_factory=dict)  # key id -> public key


def resolve_allowlist_path(
    environ: Mapping[str, str] | None = None, project: Mapping[str, Any] | None = None
) -> str | None:
    """The allow-list to use: ``SHAPE_PLUGIN_ALLOWLIST``, else ``plugins.allowlist`` of the
    project file's mapping, else ``None`` (no allow-list: nothing changes). An empty variable
    counts as unset. ``project`` is the parsed ``shape.yml``; a relative path in it is the
    caller's to resolve against the project file."""
    env = (os.environ if environ is None else environ).get(ENV_ALLOWLIST, "")
    if env.strip():
        return env
    if project is None:
        return None
    plugins = project.get("plugins")
    if plugins is None:
        return None
    if not isinstance(plugins, Mapping):
        raise AllowlistError("shape.yml: 'plugins' must be a mapping")
    path = plugins.get("allowlist")
    if path is None:
        return None
    if not isinstance(path, str) or not path.strip():
        raise AllowlistError("shape.yml: 'plugins.allowlist' must be a path")
    return path


def _fail(path: str, msg: str) -> AllowlistError:
    return AllowlistError(f"plugin allow-list {path}: {msg}")


def _parse_names(path: str, where: str, raw: Any) -> tuple[tuple[str, str], ...]:
    from shape.plugins.api import v1

    if not isinstance(raw, list) or not all(isinstance(n, str) for n in raw):
        raise _fail(path, f"{where}.names must be a list of 'group:name' strings")
    out = []
    for item in raw:
        group, sep, name = item.partition(":")
        full = group if group.startswith("shape.") else f"shape.{group}"
        if not sep or not name or full not in v1.GROUPS:
            raise _fail(
                path,
                f"{where}.names: {item!r} is not 'group:name' with a plugin group "
                f"({', '.join(short_group(g) for g in v1.GROUPS)})",
            )
        out.append((full, name))
    return tuple(out)


def _check_specifier(path: str, where: str, spec: str) -> None:
    try:
        from packaging.specifiers import InvalidSpecifier, SpecifierSet
    except ImportError:  # checked again, and failed closed, when a version is tested
        return
    try:
        SpecifierSet(spec)
    except InvalidSpecifier:
        raise _fail(path, f"{where}.version {spec!r} is not a PEP 440 version specifier") from None


def _read_document(path: str) -> Any:
    p = Path(path)
    try:
        size = p.stat().st_size
    except OSError as exc:
        raise _fail(path, f"cannot read it ({exc.strerror or exc})") from None
    if size > _MAX_ALLOWLIST_BYTES:
        raise _fail(path, f"larger than {_MAX_ALLOWLIST_BYTES} bytes")
    try:
        text = p.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise _fail(path, f"cannot read it ({exc})") from None
    try:
        return json.loads(text)
    except (ValueError, RecursionError):
        pass
    try:
        from shape.security.yamlsafe import safe_load_yaml

        return safe_load_yaml(text)
    except ImportError:
        raise _fail(
            path, "it is not JSON, and reading YAML needs pyyaml (pip install pyyaml)"
        ) from None
    except Exception as exc:  # YAMLError is not a ValueError
        raise _fail(path, f"it is neither JSON nor YAML ({type(exc).__name__})") from None


def load_allowlist(path: str | os.PathLike[str]) -> Allowlist:
    """Read and check an allow-list file (JSON or YAML). Raises :class:`AllowlistError`; a file
    that cannot be used is never treated as "no allow-list"."""
    spath = os.fspath(path)
    doc = _read_document(spath)
    if not isinstance(doc, dict):
        raise _fail(spath, "the top level must be a mapping")
    unknown = sorted(set(doc) - _TOP_KEYS)
    if unknown:
        raise _fail(spath, f"unknown key(s): {', '.join(map(str, unknown))}")
    if doc.get("format") != FORMAT:
        raise _fail(spath, f"'format' must be {FORMAT!r}")
    version = doc.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise _fail(spath, "'version' must be an integer")
    if version > VERSION:
        raise _fail(
            spath,
            f"it is version {version}, newer than this Shape reads (version {VERSION}); "
            "upgrade sqllocks-shape",
        )
    if version < 1:
        raise _fail(spath, f"unsupported version {version}")
    require = doc.get("require_signature", False)
    if not isinstance(require, bool):
        raise _fail(spath, "'require_signature' must be true or false")
    raw_plugins = doc.get("plugins")
    if not isinstance(raw_plugins, list):
        raise _fail(spath, "'plugins' must be a list")
    plugins: dict[str, AllowedPlugin] = {}
    for i, item in enumerate(raw_plugins):
        where = f"plugins[{i}]"
        if not isinstance(item, dict):
            raise _fail(spath, f"{where} must be a mapping")
        extra = sorted(set(item) - _ENTRY_KEYS)
        if extra:
            raise _fail(spath, f"{where}: unknown key(s): {', '.join(map(str, extra))}")
        dist = item.get("distribution")
        if not isinstance(dist, str) or not dist.strip():
            raise _fail(spath, f"{where}.distribution must be a distribution name")
        spec = item.get("version")
        if spec is not None:
            if not isinstance(spec, str) or not spec.strip():
                raise _fail(spath, f"{where}.version must be a PEP 440 specifier string")
            _check_specifier(spath, where, spec)
        digest = item.get("record_sha256")
        if digest is not None and not (isinstance(digest, str) and _SHA256.match(digest)):
            raise _fail(spath, f"{where}.record_sha256 must be 64 lowercase hex characters")
        names = _parse_names(spath, where, item["names"]) if "names" in item else None
        canon = canonical_name(dist)
        if canon in plugins:
            raise _fail(spath, f"{where}: distribution {dist!r} is listed twice")
        plugins[canon] = AllowedPlugin(dist, spec, digest, names)
    return Allowlist(spath, plugins, require, _load_trusted_keys(spath, doc.get("trusted_keys")))


def _load_trusted_keys(path: str, raw: Any) -> dict[str, bytes]:
    if raw is None:
        return {}
    if not isinstance(raw, list):
        raise _fail(path, "'trusted_keys' must be a list")
    from shape.artifact.keys import load_public_key

    keys: dict[str, bytes] = {}
    base = Path(path).resolve().parent
    for i, item in enumerate(raw):
        where = f"trusted_keys[{i}]"
        if not isinstance(item, dict) or set(item) != _KEY_KEYS:
            raise _fail(path, f"{where} needs exactly 'key_id' and 'public_key'")
        kid, key_path = item["key_id"], item["public_key"]
        if not isinstance(kid, str) or not isinstance(key_path, str) or not key_path:
            raise _fail(path, f"{where}: 'key_id' and 'public_key' must be strings")
        try:
            # The key file sits next to the allow-list unless its path says otherwise.
            public = load_public_key(base / key_path)
        except (OSError, ValueError) as exc:
            raise _fail(path, f"{where}: {exc}") from None
        if key_id(public) != kid:
            raise _fail(path, f"{where}: {key_path} has key id {key_id(public)}, not {kid}")
        keys[kid] = public
    return keys


# -- a distribution's files ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RecordEntry:
    path: str
    algorithm: str | None  # ``None`` when RECORD gives no hash for the file
    digest: bytes | None


def parse_record(data: bytes) -> list[RecordEntry]:
    """The rows of a ``RECORD`` file. Raises :class:`PluginTrustError` when it is malformed."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise PluginTrustError("RECORD is not UTF-8 text") from None
    out: list[RecordEntry] = []
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise PluginTrustError(f"RECORD is not valid CSV ({exc})") from None
    for row in rows:
        if not row:
            continue
        if len(row) != 3:
            raise PluginTrustError(f"RECORD has a malformed row ({len(row)} fields): {row[0]!r}")
        path, spec, _size = row
        if not spec:
            out.append(RecordEntry(path, None, None))
            continue
        algorithm, sep, b64 = spec.partition("=")
        try:
            digest = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        except (binascii.Error, ValueError):
            digest = b""
        if not sep or not digest:
            raise PluginTrustError(f"RECORD has a malformed hash for {path}")
        out.append(RecordEntry(path, algorithm.lower(), digest))
    return out


class DistFiles:
    """The files of one distribution: an installed ``.dist-info`` or a wheel."""

    info_dir: str  # e.g. ``pkg-1.0.dist-info``

    def record_bytes(self) -> bytes:
        return self.read(f"{self.info_dir}/RECORD")

    def sig_bytes(self) -> bytes | None:
        try:
            return self.read(f"{self.info_dir}/{SIGNATURE_FILE}")
        except FileNotFoundError:
            return None

    def read(self, path: str) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    @property
    def label(self) -> str:
        return self.info_dir


class _InstalledFiles(DistFiles):
    def __init__(self, dist: metadata.Distribution) -> None:
        self._dist = dist
        try:
            listed = dist.files or ()  # importlib.metadata parses RECORD here
        except csv.Error as exc:
            raise PluginTrustError(f"RECORD is not valid CSV ({exc})") from None
        record = next(
            (
                p
                for p in listed
                if p.name == "RECORD" and len(p.parts) == 2 and p.parts[0].endswith(".dist-info")
            ),
            None,
        )
        if record is None:
            raise FileNotFoundError("the distribution has no RECORD")
        self.info_dir = record.parts[0]

    def read(self, path: str) -> bytes:
        return Path(str(self._dist.locate_file(path))).read_bytes()


class _WheelFiles(DistFiles):
    def __init__(self, archive: zipfile.ZipFile) -> None:
        self._zip = archive
        infos = sorted(
            {
                n.split("/", 1)[0]
                for n in archive.namelist()
                if n.split("/", 1)[0].endswith(".dist-info")
            }
        )
        if len(infos) != 1:
            raise PluginTrustError(
                f"a wheel has exactly one .dist-info folder; this one has {len(infos)}"
            )
        self.info_dir = infos[0]

    def read(self, path: str) -> bytes:
        try:
            return self._zip.read(path)
        except KeyError:
            raise FileNotFoundError(path) from None


def installed_files(dist: metadata.Distribution) -> DistFiles:
    """The files of an installed distribution; ``FileNotFoundError`` when it has no RECORD."""
    return _InstalledFiles(dist)


def verify_files(files: DistFiles) -> None:
    """Every file RECORD gives a hash for must have that hash: raises :class:`PluginTrustError`
    naming the first one that is missing or changed (in RECORD order)."""
    try:
        entries = parse_record(files.record_bytes())
    except FileNotFoundError:
        raise PluginTrustError(f"{files.info_dir}/RECORD is missing") from None
    for e in entries:
        if e.digest is None or e.algorithm is None:
            continue
        if e.algorithm not in hashlib.algorithms_guaranteed or e.algorithm.startswith("shake_"):
            raise PluginTrustError(f"{e.path}: RECORD uses the unsupported hash {e.algorithm!r}")
        try:
            data = files.read(e.path)
        except FileNotFoundError:
            raise PluginTrustError(f"file {e.path} listed in RECORD is missing") from None
        except OSError as exc:
            raise PluginTrustError(f"file {e.path} cannot be read ({exc.strerror})") from None
        if hashlib.new(e.algorithm, data).digest() != e.digest:
            raise PluginTrustError(f"file {e.path} was changed after installation")


def record_sha256(files: DistFiles) -> str:
    return hashlib.sha256(files.record_bytes()).hexdigest()


def _signed_list(files: DistFiles) -> bytes:
    """The canonical file list: sorted ``path sha256`` lines of the files RECORD hashes, without
    RECORD, the signature file, what an installer adds and what lies outside the install folder."""
    skip = {f"{files.info_dir}/RECORD", f"{files.info_dir}/{SIGNATURE_FILE}"}
    skip |= {f"{files.info_dir}/{n}" for n in _INSTALLER_FILES}
    lines = []
    for e in parse_record(files.record_bytes()):
        if e.digest is None or e.path in skip or e.path.startswith("../") or "/../" in e.path:
            continue
        if e.algorithm != "sha256":
            raise PluginTrustError(
                f"{e.path}: RECORD uses {e.algorithm!r}; a signature needs sha256 hashes"
            )
        if "\n" in e.path or "\r" in e.path:
            # One line per file: a line break in a path could make another file list produce the
            # same bytes (and so accept an edited file under the same signature).
            raise PluginTrustError(
                f"RECORD path {e.path!r} contains a line break, which a signature cannot cover"
            )
        lines.append(f"{e.path} {e.digest.hex()}")
    return ("".join(f"{line}\n" for line in sorted(lines))).encode("utf-8")


def _message(files: DistFiles) -> bytes:
    return _DOMAIN + _signed_list(files)


def _parse_signature(blob: bytes, where: str) -> tuple[str, bytes]:
    try:
        doc = json.loads(blob)
        if not isinstance(doc, dict):
            raise ValueError("not an object")
        if doc.get("format") != SIGNATURE_FORMAT:
            raise ValueError("wrong format")
        ver = doc.get("version")
        if isinstance(ver, bool) or not isinstance(ver, int):
            raise ValueError("no integer version")
        if ver > SIGNATURE_VERSION:
            raise PluginTrustError(
                f"{where} is version {ver}, newer than this Shape reads; upgrade sqllocks-shape"
            )
        if ver < 1:
            raise ValueError(f"unsupported version {ver}")
        if doc.get("algorithm") != ALGORITHM:
            raise ValueError(f"unsupported algorithm {doc.get('algorithm')!r}")
        sig = base64.b64decode(str(doc["signature"]), validate=True)
        kid = doc["key_id"]
        if not isinstance(kid, str):
            raise ValueError("key_id is not a string")
    except PluginTrustError:
        raise
    except (ValueError, KeyError, binascii.Error, RecursionError) as exc:
        raise PluginTrustError(f"{where} is malformed ({exc})") from None
    return kid, sig


def verify_signature(files: DistFiles, trusted: Mapping[str, bytes]) -> str:
    """Check ``shape-plugin.sig``: signed by a key in ``trusted``, over the file list, and every
    file still has the hash RECORD gives it. Returns the key id; raises
    :class:`PluginTrustError` (:class:`MissingCryptoError` without ``cryptography``)."""
    blob = files.sig_bytes()
    if blob is None:
        raise PluginTrustError(f"not signed: {files.info_dir}/{SIGNATURE_FILE} is missing")
    kid, sig = _parse_signature(blob, SIGNATURE_FILE)
    public = trusted.get(kid)
    if public is None:
        raise PluginTrustError(f"signed by key {kid}, which is not a trusted key")
    if not crypto_available():
        raise MissingCryptoError(
            f"checking a signature needs the cryptography package: {SIGN_HINT}"
        )
    from shape.security.crypto import verify_ed25519

    try:
        message = _message(files)
    except FileNotFoundError:
        raise PluginTrustError(f"{files.info_dir}/RECORD is missing") from None
    try:
        verify_ed25519(message, sig, public)
    except ShapeError:
        raise PluginTrustError(
            "the signature does not match the distribution's file list"
        ) from None
    verify_files(files)
    return kid


# -- signing a wheel ----------------------------------------------------------------------


def _record_row(path: str, data: bytes) -> list[str]:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return [path, f"sha256={digest}", str(len(data))]


def sign_wheel(
    wheel: str | os.PathLike[str],
    private_key: bytes,
    out: str | os.PathLike[str] | None = None,
) -> str:
    """Sign the wheel (in place, or into ``out``) and return the key id.

    The wheel's files must match its RECORD first, so a damaged wheel is never signed. The
    signature file is added to the wheel and to its RECORD; signing again replaces it."""
    if len(private_key) != _KEY_BYTES:
        raise ValueError("private key must be 32 bytes")
    if not crypto_available():
        raise MissingCryptoError(f"signing needs the cryptography package: {SIGN_HINT}")
    from shape.security.crypto import public_key_from_private, sign_ed25519

    src = Path(wheel)
    with zipfile.ZipFile(src) as zin:
        files = _WheelFiles(zin)
        if any(".data/" in n for n in zin.namelist()):
            raise PluginTrustError(
                "this wheel has a .data folder, whose files are installed to other paths; "
                "shape plugins sign handles wheels without one"
            )
        verify_files(files)
        public = public_key_from_private(private_key)
        sig = sign_ed25519(_message(files), private_key)
        sig_doc = {
            "format": SIGNATURE_FORMAT,
            "version": SIGNATURE_VERSION,
            "algorithm": ALGORITHM,
            "key_id": key_id(public),
            "signature": base64.b64encode(sig).decode(),
        }
        sig_bytes = (json.dumps(sig_doc, sort_keys=True, separators=(",", ":")) + "\n").encode()
        info = files.info_dir
        sig_path, record_path = f"{info}/{SIGNATURE_FILE}", f"{info}/RECORD"
        rows = list(csv.reader(io.StringIO(files.record_bytes().decode("utf-8"), newline="")))
        buf = io.StringIO()
        writer = csv.writer(buf, lineterminator="\n")  # quotes a path that holds a comma or quote
        writer.writerows(r for r in rows if r and r[0] not in (record_path, sig_path))
        writer.writerow(_record_row(sig_path, sig_bytes))
        writer.writerow([record_path, "", ""])
        record = buf.getvalue()
        target = Path(out) if out is not None else src
        if not target.resolve().parent.is_dir():
            raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(target))
        fd, tmp_name = tempfile.mkstemp(dir=str(target.resolve().parent), suffix=".tmp")
        os.close(fd)
        try:
            os.chmod(tmp_name, stat.S_IMODE(src.stat().st_mode))  # mkstemp makes it 0600
            with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zout:
                for item in zin.infolist():
                    if item.filename in (record_path, sig_path):
                        continue
                    zout.writestr(item, zin.read(item.filename))
                zout.writestr(sig_path, sig_bytes)
                zout.writestr(record_path, record.encode("utf-8"))
            zin.close()
            os.replace(tmp_name, target)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise
    return key_id(public)


def open_wheel(path: str | os.PathLike[str]) -> tuple[zipfile.ZipFile, DistFiles]:
    """Open a wheel for reading; the caller closes the archive."""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise PluginTrustError(f"{path} is not a wheel (not a zip archive)") from None
    try:
        return archive, _WheelFiles(archive)
    except BaseException:
        archive.close()
        raise


# -- the host's decision ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Decision:
    """``reason`` is ``None`` when the plugin may load."""

    reason: str | None = None
    kind: str | None = None  # NOT_PERMITTED or CHECK_FAILED


ALLOWED = Decision()


class Enforcer:
    """The allow-list as the host applies it. One per host; caches the per-distribution file and
    signature checks, which read every file of the distribution."""

    def __init__(self, allowlist: Allowlist | None, error: str | None = None) -> None:
        self.allowlist = allowlist
        self.error = error  # why the allow-list cannot be used: every plugin is then blocked
        self._dist_cache: dict[str, str | None] = {}

    @property
    def path(self) -> str:
        return self.allowlist.path if self.allowlist else ""

    def decide(
        self, group: str, name: str, dist: metadata.Distribution | None, dist_name: str
    ) -> Decision:
        who = f"plugin {short_group(group)}:{name}"
        if self.error is not None:
            return Decision(f"{who} is blocked: {self.error}", CHECK_FAILED)
        al = self.allowlist
        assert al is not None
        version = (dist.version if dist is not None else None) or "unknown"
        tag = f"{who} ({dist_name} {version})"
        entry = al.plugins.get(canonical_name(dist_name))
        if dist is None or entry is None:
            return Decision(f"{tag} is not on the plugin allow-list {al.path}", NOT_PERMITTED)
        if entry.names is not None and (group, name) not in entry.names:
            allowed = ", ".join(f"{short_group(g)}:{n}" for g, n in entry.names)
            return Decision(
                f"{tag} is not on the plugin allow-list {al.path}: the list allows "
                f"{dist_name} only for {allowed}",
                NOT_PERMITTED,
            )
        if entry.version is not None:
            problem = _version_problem(version, entry.version)
            if problem:
                return Decision(
                    f"{tag} is blocked by the plugin allow-list {al.path}: {problem}", CHECK_FAILED
                )
        key = canonical_name(dist_name)
        if key not in self._dist_cache:
            self._dist_cache[key] = self._dist_problem(entry, dist)
        problem = self._dist_cache[key]
        if problem:
            return Decision(
                f"{tag} is blocked by the plugin allow-list {al.path}: {problem}", CHECK_FAILED
            )
        return ALLOWED

    def _dist_problem(self, entry: AllowedPlugin, dist: metadata.Distribution) -> str | None:
        al = self.allowlist
        assert al is not None
        if entry.record_sha256 is None and not al.require_signature:
            return None
        try:
            files = installed_files(dist)
            if entry.record_sha256 is not None:
                if record_sha256(files) != entry.record_sha256:
                    return "its RECORD does not match record_sha256 in the allow-list"
                verify_files(files)
            if al.require_signature:
                verify_signature(files, al.trusted_keys)
        except FileNotFoundError:
            return "the distribution has no RECORD to check"
        except PluginTrustError as exc:
            return str(exc)
        except OSError as exc:
            return f"its files cannot be read ({exc.strerror or exc})"
        return None


def _version_problem(version: str, spec: str) -> str | None:
    try:
        from packaging.specifiers import SpecifierSet
        from packaging.version import InvalidVersion, Version
    except ImportError:
        return "checking a version specifier needs the 'packaging' package (pip install packaging)"
    try:
        ok = SpecifierSet(spec).contains(Version(version), prereleases=True)
    except InvalidVersion:
        return f"version {version!r} is not a PEP 440 version"
    return None if ok else f"version {version} does not satisfy {spec}"


def make_enforcer(path: str | None, require_crypto: bool = True) -> Enforcer | None:
    """The enforcer for the allow-list at ``path``; ``None`` when there is none. A file that
    cannot be read, and a signature requirement without ``cryptography``, give an enforcer that
    blocks every non-built-in plugin (fail closed) instead of an exception: commands that use no
    plugin still run."""
    if path is None:
        return None
    try:
        al = load_allowlist(path)
    except AllowlistError as exc:
        return Enforcer(None, f"the allow-list cannot be used: {exc}")
    if al.require_signature and require_crypto and not crypto_available():
        return Enforcer(
            al,
            f"the allow-list {al.path} requires signatures, which needs the cryptography "
            f"package: {SIGN_HINT}",
        )
    return Enforcer(al)


def is_core(dist_name: str) -> bool:
    return canonical_name(dist_name) == CORE_DISTRIBUTION


def is_builtin(dist_name: str, target: str) -> bool:
    """Core's own entry point: the core distribution's name *and* a target in the ``shape``
    package. The name alone is what a distribution says about itself; every built-in's target
    is a ``shape.`` module, so another distribution that takes core's name is still checked."""
    module = target.partition(":")[0].strip()
    return is_core(dist_name) and (module == "shape" or module.startswith("shape."))
