"""W1-18: plugin allow-list, installed-file check, opt-in signatures, listing and doctor.

Test plugin distributions are built as real wheels, signed with ``shape plugins sign`` where the
case needs it, and installed (extracted, with the extra RECORD rows pip adds) into a temporary
path that is put on ``sys.path``. Each blocked case asserts the plugin module was never imported.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from shape.plugins import trust
from shape.plugins.api import v1
from shape.plugins.host import PluginBlockedError, PluginHost, PluginLoadError

pytestmark = pytest.mark.contract

BODY = """
SHAPE_API = "1.0"

class Det:
    name = "{name}"
    def detect(self, values, column):
        return None
"""


def _row(path: str, data: bytes | None) -> list[str]:
    if data is None:
        return [path, "", ""]
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return [path, f"sha256={digest}", str(len(data))]


def build_wheel(
    out: Path,
    dist: str = "acme-plugin",
    version: str = "1.0",
    module: str = "acme_mod",
    names: tuple[str, ...] = ("one",),
    group: str = "shape.detectors",
    extra_members: dict[str, bytes] | None = None,
) -> Path:
    base = dist.replace("-", "_")
    info = f"{base}-{version}.dist-info"
    members = {
        f"{module}.py": BODY.format(name=names[0]).encode(),
        f"{info}/METADATA": f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n".encode(),
        f"{info}/WHEEL": b"Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\n",
        f"{info}/entry_points.txt": (
            f"[{group}]\n" + "".join(f"{n} = {module}:Det\n" for n in names)
        ).encode(),
        **(extra_members or {}),
    }
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for path, data in members.items():
        w.writerow(_row(path, data))
    w.writerow(_row(f"{info}/RECORD", None))
    members[f"{info}/RECORD"] = buf.getvalue().encode()
    wheel = out / f"{base}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for path, data in members.items():
            z.writestr(path, data)
    return wheel


def install(wheel: Path, site: Path) -> Path:
    """Extract like an installer: also adds INSTALLER and a ``.pyc`` row to RECORD, which the
    wheel's RECORD does not have."""
    with zipfile.ZipFile(wheel) as z:
        z.extractall(site)
        info = next(n.split("/")[0] for n in z.namelist() if n.endswith(".dist-info/METADATA"))
    (site / info / "INSTALLER").write_text("pip\n")
    with open(site / info / "RECORD", "a", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(_row(f"{info}/INSTALLER", b"pip\n"))
        w.writerow(["__pycache__/acme_mod.cpython-311.pyc", "", ""])
    return site / info


def keypair(tmp: Path, label: str = "k") -> tuple[bytes, bytes, Path]:
    from shape.artifact.keys import KEY_BYTES  # noqa: F401
    from shape.security.crypto import generate_ed25519_keypair

    sk, pk = generate_ed25519_keypair()
    pub = tmp / f"{label}.pub"
    pub.write_text(base64.b64encode(pk).decode() + "\n")
    return sk, pk, pub


def write_allowlist(path: Path, plugins: list[dict], **kw) -> Path:
    doc = {"format": "shape-plugin-allowlist", "version": 1, "plugins": plugins, **kw}
    path.write_text(json.dumps(doc))
    return path


def host_for(site: Path, allowlist: Path | None) -> PluginHost:
    """Installed-package discovery of ``site`` only (plus the allow-list), no core built-ins."""
    from importlib import metadata

    def eps():
        return [
            ep
            for g in v1.GROUPS
            for ep in metadata.entry_points(group=g)
            if ep.dist is not None
            and ep.dist.name != "sqllocks-shape"
            and ep.dist.name in {"acme-plugin", "other-plugin"}
        ]

    return PluginHost(entry_points=eps, allowlist=allowlist)


@pytest.fixture
def site(tmp_path, monkeypatch):
    s = tmp_path / "site"
    s.mkdir()
    for m in ("acme_mod", "other_mod"):
        monkeypatch.delitem(sys.modules, m, raising=False)
    monkeypatch.syspath_prepend(str(s))
    monkeypatch.delenv(trust.ENV_ALLOWLIST, raising=False)
    return s


def put(site: Path, tmp_path: Path, **kw) -> Path:
    wheels = tmp_path / "wheels"
    wheels.mkdir(exist_ok=True)
    return install(build_wheel(wheels, **kw), site)


# -- 1. the allow-list file ---------------------------------------------------------------


def test_load_json_and_defaults(tmp_path):
    p = write_allowlist(tmp_path / "a.json", [{"distribution": "Acme_Plugin"}])
    al = trust.load_allowlist(p)
    assert not al.require_signature and not al.trusted_keys
    assert al.plugins["acme-plugin"].version is None and al.plugins["acme-plugin"].names is None


def test_load_yaml_with_every_field(tmp_path):
    yaml = pytest.importorskip("yaml")
    _, pk, pub = keypair(tmp_path)
    doc = {
        "format": "shape-plugin-allowlist",
        "version": 1,
        "plugins": [
            {
                "distribution": "sqllocks-shape-kafka",
                "version": "==0.9.0",
                "record_sha256": "a" * 64,
                "names": ["sources:kafka", "shape.emitters:kafka"],
            }
        ],
        "require_signature": True,
        "trusted_keys": [{"key_id": trust.key_id(pk), "public_key": pub.name}],
    }
    (tmp_path / "a.yml").write_text(yaml.safe_dump(doc))
    al = trust.load_allowlist(tmp_path / "a.yml")
    e = al.plugins["sqllocks-shape-kafka"]
    assert e.names == (("shape.sources", "kafka"), ("shape.emitters", "kafka"))
    assert al.require_signature and al.trusted_keys == {trust.key_id(pk): pk}


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda d: d.update(format="other"), "'format'"),
        (lambda d: d.update(version=2), "newer than this Shape"),
        (lambda d: d.update(version=0), "unsupported version"),
        (lambda d: d.update(version="1"), "integer"),
        (lambda d: d.update(version=True), "integer"),
        (lambda d: d.update(extra=1), "unknown key"),
        (lambda d: d.update(plugins={}), "'plugins' must be a list"),
        (lambda d: d.update(require_signature="yes"), "require_signature"),
        (lambda d: d["plugins"].append({"distribution": "x", "oops": 1}), "unknown key"),
        (lambda d: d["plugins"].append({"distribution": ""}), "distribution"),
        (lambda d: d["plugins"].append({"distribution": "x", "version": "banana"}), "PEP 440"),
        (lambda d: d["plugins"].append({"distribution": "x", "record_sha256": "AB"}), "64"),
        (lambda d: d["plugins"].append({"distribution": "x", "names": ["nogroup"]}), "group:name"),
        (lambda d: d["plugins"].append({"distribution": "x", "names": ["bogus:n"]}), "group"),
        (lambda d: d["plugins"].append({"distribution": "ACME_plugin"}), "twice"),
        (lambda d: d.update(trusted_keys=[{"key_id": "x"}]), "exactly"),
        (
            lambda d: d.update(trusted_keys=[{"key_id": "x", "public_key": "missing.pub"}]),
            "missing",
        ),
    ],
)
def test_invalid_allowlists_are_rejected(tmp_path, mutate, message):
    doc = {
        "format": "shape-plugin-allowlist",
        "version": 1,
        "plugins": [{"distribution": "acme-plugin"}],
    }
    mutate(doc)
    p = tmp_path / "a.json"
    p.write_text(json.dumps(doc))
    with pytest.raises(trust.AllowlistError, match=message):
        trust.load_allowlist(p)


def test_key_id_must_match_the_key_file(tmp_path):
    _, _, pub = keypair(tmp_path)
    p = write_allowlist(
        tmp_path / "a.json", [], trusted_keys=[{"key_id": "0" * 16, "public_key": pub.name}]
    )
    with pytest.raises(trust.AllowlistError, match="has key id"):
        trust.load_allowlist(p)


@pytest.mark.parametrize("text", ["", "[]", "{not json", "null"])
def test_garbage_and_missing_files(tmp_path, text):
    p = tmp_path / "a.json"
    p.write_text(text)
    with pytest.raises(trust.AllowlistError):
        trust.load_allowlist(p)
    with pytest.raises(trust.AllowlistError, match="cannot read"):
        trust.load_allowlist(tmp_path / "nope.json")


def test_oversized_allowlist(tmp_path):
    p = tmp_path / "a.json"
    p.write_text(" " * (1024 * 1024 + 1))
    with pytest.raises(trust.AllowlistError, match="larger than"):
        trust.load_allowlist(p)


def test_schema_file_matches_the_loader():
    path = Path(trust.__file__).parents[1] / "schemas" / "plugin-allowlist-v1.schema.json"
    schema = json.loads(path.read_text())
    assert schema["properties"]["format"]["const"] == trust.FORMAT
    assert schema["properties"]["version"]["const"] == trust.VERSION
    assert set(schema["properties"]) == trust._TOP_KEYS
    entry = schema["$defs"]["plugin"]
    assert set(entry["properties"]) == trust._ENTRY_KEYS and entry["required"] == ["distribution"]
    assert set(schema["$defs"]["key"]["properties"]) == trust._KEY_KEYS
    assert schema["additionalProperties"] is False and entry["additionalProperties"] is False


# -- 2. activation ------------------------------------------------------------------------


def test_env_wins_over_project_and_neither_means_none():
    proj = {"plugins": {"allowlist": "from-project.json"}}
    assert trust.resolve_allowlist_path({}, None) is None
    assert trust.resolve_allowlist_path({}, {}) is None
    assert trust.resolve_allowlist_path({}, proj) == "from-project.json"
    assert trust.resolve_allowlist_path({"SHAPE_PLUGIN_ALLOWLIST": "env.json"}, proj) == "env.json"
    assert (
        trust.resolve_allowlist_path({"SHAPE_PLUGIN_ALLOWLIST": " "}, proj) == "from-project.json"
    )


@pytest.mark.parametrize(
    "bad", [{"plugins": []}, {"plugins": {"allowlist": 3}}, {"plugins": {"allowlist": ""}}]
)
def test_bad_project_config(bad):
    with pytest.raises(trust.AllowlistError):
        trust.resolve_allowlist_path({}, bad)


def test_host_reads_env_variable_and_project_config(site, tmp_path, monkeypatch):
    put(site, tmp_path)
    empty = write_allowlist(tmp_path / "empty.json", [])
    monkeypatch.setenv(trust.ENV_ALLOWLIST, str(empty))
    assert host_for(site, None).record("shape.detectors", "one").status == "blocked"
    monkeypatch.delenv(trust.ENV_ALLOWLIST)
    host = PluginHost(project_config={"plugins": {"allowlist": str(empty)}})
    assert host.allowlist is not None and host.allowlist.path == str(empty)


def test_no_allowlist_changes_nothing(site, tmp_path):
    put(site, tmp_path)
    host = host_for(site, None)
    assert not host.allowlist_active
    assert host.get("shape.detectors", "one").name == "one"


def test_builtins_are_always_allowed(tmp_path):
    from shape.plugins.registry import register_builtins

    empty = write_allowlist(tmp_path / "empty.json", [], require_signature=True)
    host = PluginHost(allowlist=empty)
    register_builtins(host)
    assert host.allowlist_active
    assert host.get("shape.sinks", "parquet") is not None
    assert not [r for r in host.records() if r.status == "blocked" and r.source == "sqllocks-shape"]


# -- 3. enforcement before import ---------------------------------------------------------


def assert_blocked(host, site, reason, kind="not_permitted"):
    rec = host.record("shape.detectors", "one")
    assert rec.status == "blocked" and reason in rec.error and rec.blocked_kind == kind
    host.load_all()
    assert "acme_mod" not in sys.modules
    with pytest.raises(PluginBlockedError) as exc:
        host.get("shape.detectors", "one")
    assert isinstance(exc.value, PluginLoadError) and reason in str(exc.value)
    assert host.try_get("shape.detectors", "one") is None
    assert "acme_mod" not in sys.modules


def test_allowed(site, tmp_path):
    put(site, tmp_path)
    al = write_allowlist(
        tmp_path / "a.json",
        [{"distribution": "acme_plugin", "version": ">=1.0,<2", "names": ["detectors:one"]}],
    )
    host = host_for(site, al)
    assert host.record("shape.detectors", "one").status == "unloaded"
    assert host.get("shape.detectors", "one").name == "one"


def test_not_listed(site, tmp_path):
    put(site, tmp_path)
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "other-plugin"}])
    assert_blocked(
        host_for(site, al),
        site,
        f"plugin detectors:one (acme-plugin 1.0) is not on the plugin allow-list {al}",
    )


@pytest.mark.parametrize(
    "spec, ok",
    [
        ("==1.0", True),
        ("==1.0.0", True),
        (">=1.0", True),
        (">1.0", False),
        ("==2.0", False),
        ("<1.0", False),
    ],
)
def test_version_specifier_boundaries(site, tmp_path, spec, ok):
    put(site, tmp_path)
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "acme-plugin", "version": spec}])
    host = host_for(site, al)
    if ok:
        assert host.record("shape.detectors", "one").status == "unloaded"
    else:
        assert_blocked(host, site, f"version 1.0 does not satisfy {spec}", "check_failed")


def test_prerelease_version_can_be_pinned(site, tmp_path):
    put(site, tmp_path, version="1.0a1")
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "version": "==1.0a1"}]
    )
    assert host_for(site, al).get("shape.detectors", "one").name == "one"


def test_name_not_in_names(site, tmp_path):
    put(site, tmp_path, names=("one", "two"))
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "names": ["shape.detectors:two"]}]
    )
    host = host_for(site, al)
    rec = host.record("shape.detectors", "one")
    assert rec.status == "blocked" and "only for detectors:two" in rec.error
    assert host.record("shape.detectors", "two").status == "unloaded"
    with pytest.raises(PluginBlockedError):
        host.get("shape.detectors", "one")
    assert "acme_mod" not in sys.modules  # "two" shares the module but has not been loaded


def test_a_blocked_plugin_does_not_stop_the_others(site, tmp_path, monkeypatch):
    put(site, tmp_path, dist="acme-plugin", module="acme_mod")
    wheels = tmp_path / "w2"
    wheels.mkdir()
    install(build_wheel(wheels, dist="other-plugin", module="other_mod", names=("two",)), site)
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "other-plugin"}])
    host = host_for(site, al)
    assert host.get("shape.detectors", "two") is not None
    assert "acme_mod" not in sys.modules
    assert host.record("shape.detectors", "one").status == "blocked"


def test_unreadable_allowlist_fails_closed_without_raising(site, tmp_path):
    put(site, tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text("{nope")
    host = host_for(site, bad)
    assert host.allowlist is None and host.allowlist_active
    assert_blocked(host, site, "the allow-list cannot be used", "check_failed")


def test_cli_prints_the_error_and_exits_2(site, tmp_path):
    put(site, tmp_path, group="shape.commands", names=("hello",))
    (site / "acme_mod.py").write_text('SHAPE_API = "1.0"\nraise SystemExit("imported")\n')
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "other-plugin"}])
    r = shape(site, al, "hello")
    assert r.returncode == 2, r.stderr
    assert r.stderr.strip() == (
        "shape: error: plugin commands:hello (acme-plugin 1.0) is not on the plugin allow-list "
        f"{al}"
    )
    assert "imported" not in r.stderr + r.stdout
    # A command that does not use the plugin is unaffected.
    assert shape(site, al, "version").returncode == 0


def shape(site, allowlist, *args):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(site), env.get("PYTHONPATH", "")])
    env.pop(trust.ENV_ALLOWLIST, None)
    if allowlist is not None:
        env[trust.ENV_ALLOWLIST] = str(allowlist)
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.cli.main import main; sys.exit(main())",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
    )


# -- 4. installed-file check --------------------------------------------------------------


def record_hash(info: Path) -> str:
    return hashlib.sha256((info / "RECORD").read_bytes()).hexdigest()


def test_record_hash_matches(site, tmp_path):
    info = put(site, tmp_path)
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": record_hash(info)}]
    )
    assert host_for(site, al).get("shape.detectors", "one").name == "one"


def test_record_hash_mismatch(site, tmp_path):
    put(site, tmp_path)
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": "0" * 64}]
    )
    assert_blocked(host_for(site, al), site, "RECORD does not match record_sha256", "check_failed")


def test_edited_file_names_the_first_changed_file(site, tmp_path):
    info = put(site, tmp_path)
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": record_hash(info)}]
    )
    (site / "acme_mod.py").write_text('SHAPE_API = "1.0"\nraise SystemExit("evil")\n')
    assert_blocked(
        host_for(site, al), site, "file acme_mod.py was changed after installation", "check_failed"
    )


def test_edited_metadata_file_is_named_too(site, tmp_path):
    info = put(site, tmp_path)
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": record_hash(info)}]
    )
    ep = info / "entry_points.txt"
    ep.write_text(ep.read_text() + "\n")
    # entry_points.txt comes after the module in RECORD; the module is intact, so this is first.
    assert_blocked(
        host_for(site, al),
        site,
        "entry_points.txt was changed after installation",
        "check_failed",
    )


def test_deleted_file_blocks(site, tmp_path):
    info = put(site, tmp_path)
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": record_hash(info)}]
    )
    (info / "WHEEL").unlink()
    assert_blocked(host_for(site, al), site, "WHEEL listed in RECORD is missing", "check_failed")


def test_edited_record_blocks_even_when_files_are_intact(site, tmp_path):
    info = put(site, tmp_path)
    digest = record_hash(info)
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": digest}]
    )
    with open(info / "RECORD", "a") as fh:
        fh.write("extra.py,,\n")
    assert_blocked(host_for(site, al), site, "RECORD does not match", "check_failed")


def test_files_are_not_checked_without_record_sha256(site, tmp_path):
    put(site, tmp_path)
    (site / "acme_mod.py").write_text(BODY.format(name="one") + "\n# edited\n")
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "acme-plugin"}])
    assert host_for(site, al).get("shape.detectors", "one").name == "one"


def test_missing_record_blocks_when_a_hash_is_pinned(site, tmp_path):
    info = put(site, tmp_path)
    (info / "RECORD").unlink()
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": "0" * 64}]
    )
    assert_blocked(host_for(site, al), site, "no RECORD", "check_failed")


# -- 5. signatures ------------------------------------------------------------------------


def sign_into(site, tmp_path, key: bytes, **kw) -> Path:
    wheels = tmp_path / "wheels"
    wheels.mkdir(exist_ok=True)
    wheel = build_wheel(wheels, **kw)
    trust.sign_wheel(wheel, key)
    return install(wheel, site)


def signed_allowlist(tmp_path, pub, pk, **kw):
    return write_allowlist(
        tmp_path / "a.json",
        [{"distribution": "acme-plugin"}],
        require_signature=True,
        trusted_keys=[{"key_id": trust.key_id(pk), "public_key": str(pub)}],
        **kw,
    )


@pytest.mark.sign
def test_valid_signature_loads(site, tmp_path):
    sk, pk, pub = keypair(tmp_path)
    sign_into(site, tmp_path, sk)
    host = host_for(site, signed_allowlist(tmp_path, pub, pk))
    assert host.get("shape.detectors", "one").name == "one"


@pytest.mark.sign
def test_signature_by_untrusted_key(site, tmp_path):
    sk, _, _ = keypair(tmp_path, "mine")
    _, pk2, pub2 = keypair(tmp_path, "theirs")
    sign_into(site, tmp_path, sk)
    host = host_for(site, signed_allowlist(tmp_path, pub2, pk2))
    assert_blocked(host, site, "which is not a trusted key", "check_failed")


@pytest.mark.sign
def test_missing_signature_with_require_signature(site, tmp_path):
    _, pk, pub = keypair(tmp_path)
    put(site, tmp_path)
    host = host_for(site, signed_allowlist(tmp_path, pub, pk))
    assert_blocked(host, site, "not signed", "check_failed")


@pytest.mark.sign
def test_signature_is_ignored_unless_required(site, tmp_path):
    put(site, tmp_path)
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "acme-plugin"}])
    assert host_for(site, al).get("shape.detectors", "one").name == "one"


@pytest.mark.sign
def test_file_edited_after_signing(site, tmp_path):
    sk, pk, pub = keypair(tmp_path)
    sign_into(site, tmp_path, sk)
    (site / "acme_mod.py").write_text('SHAPE_API = "1.0"\nraise SystemExit("evil")\n')
    host = host_for(site, signed_allowlist(tmp_path, pub, pk))
    assert_blocked(host, site, "file acme_mod.py was changed", "check_failed")


@pytest.mark.sign
def test_record_rewritten_after_signing(site, tmp_path):
    """Editing a file and fixing its RECORD hash changes the file list: the signature fails."""
    sk, pk, pub = keypair(tmp_path)
    info = sign_into(site, tmp_path, sk)
    evil = b'SHAPE_API = "1.0"\nraise SystemExit("evil")\n'
    (site / "acme_mod.py").write_bytes(evil)
    rows = list(csv.reader((info / "RECORD").read_text().splitlines()))
    rows = [_row("acme_mod.py", evil) if r[0] == "acme_mod.py" else r for r in rows]
    with open(info / "RECORD", "w", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(rows)
    host = host_for(site, signed_allowlist(tmp_path, pub, pk))
    assert_blocked(host, site, "signature does not match", "check_failed")


@pytest.mark.sign
@pytest.mark.parametrize(
    "sig, message",
    [
        (b"not json", "malformed"),
        (
            b'{"format":"shape-plugin-signature","version":1,"algorithm":"RSA","key_id":"x","signature":"AA=="}',
            "unsupported algorithm",
        ),
        (b'{"format":"other","version":1}', "malformed"),
        (
            b'{"format":"shape-plugin-signature","version":2,"algorithm":"Ed25519","key_id":"x","signature":"AA=="}',
            "newer",
        ),
    ],
)
def test_malformed_signature_files(site, tmp_path, sig, message):
    sk, pk, pub = keypair(tmp_path)
    info = sign_into(site, tmp_path, sk)
    (info / "shape-plugin.sig").write_bytes(sig)
    host = host_for(site, signed_allowlist(tmp_path, pub, pk))
    assert_blocked(host, site, message, "check_failed")


def test_missing_cryptography_fails_closed(site, tmp_path, monkeypatch):
    _, pk, pub = keypair(tmp_path)
    put(site, tmp_path)
    monkeypatch.setattr(trust, "crypto_available", lambda: False)
    host = host_for(site, signed_allowlist(tmp_path, pub, pk))
    assert_blocked(host, site, "pip install 'sqllocks-shape[sign]'", "check_failed")
    from shape.plugins.registry import register_builtins

    register_builtins(host)
    assert host.get("shape.sinks", "parquet") is not None  # built-ins still load


# -- sign and verify commands -------------------------------------------------------------


def run_cli(*args, env=None):
    from shape.cli.main import main

    return main(list(args))


@pytest.mark.sign
def test_signed_wheel_round_trip(site, tmp_path, capsys):
    sk, pk, pub = keypair(tmp_path)
    key = tmp_path / "k.key"
    key.write_text(base64.b64encode(sk).decode() + "\n")
    wheel = build_wheel(tmp_path)
    assert run_cli("plugins", "verify", str(wheel), "--key", str(pub)) == 1
    assert "not signed" in capsys.readouterr().err
    assert run_cli("plugins", "sign", str(wheel), "--key", str(key)) == 0
    assert run_cli("plugins", "verify", str(wheel), "--key", str(pub)) == 0
    # The wheel's RECORD lists the signature with a correct hash and still verifies file by file.
    with zipfile.ZipFile(wheel) as z:
        trust.verify_files(trust._WheelFiles(z))
        assert "acme_plugin-1.0.dist-info/shape-plugin.sig" in z.namelist()
    info = install(wheel, site)
    assert run_cli("plugins", "verify", "acme-plugin", "--key", str(pub)) == 0
    assert run_cli("plugins", "verify", "acme-plugin", "--json", "--key", str(pub)) == 0
    assert json.loads(capsys.readouterr().out.splitlines()[-1])["valid"] is True
    (site / "acme_mod.py").write_text("# edited\n")
    assert run_cli("plugins", "verify", "acme-plugin", "--key", str(pub)) == 1
    assert "acme_mod.py was changed" in capsys.readouterr().err
    del info


@pytest.mark.sign
def test_sign_with_output_leaves_the_original_and_resigning_replaces(tmp_path):
    sk, pk, pub = keypair(tmp_path)
    sk2, pk2, pub2 = keypair(tmp_path, "second")
    wheel = build_wheel(tmp_path)
    before = wheel.read_bytes()
    out = tmp_path / "signed.whl"
    trust.sign_wheel(wheel, sk, out)
    assert wheel.read_bytes() == before
    trust.sign_wheel(out, sk2)
    with zipfile.ZipFile(out) as z:
        files = trust._WheelFiles(z)
        assert trust.verify_signature(files, {trust.key_id(pk2): pk2}) == trust.key_id(pk2)
        with pytest.raises(trust.PluginTrustError, match="not a trusted key"):
            trust.verify_signature(files, {trust.key_id(pk): pk})
        assert z.namelist().count("acme_plugin-1.0.dist-info/shape-plugin.sig") == 1


@pytest.mark.sign
def test_signature_survives_a_real_pip_install(tmp_path):
    sk, pk, pub = keypair(tmp_path)
    wheel = build_wheel(tmp_path)
    trust.sign_wheel(wheel, sk)
    target = tmp_path / "t"
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-index",
            "--target",
            str(target),
            str(wheel),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr
    from importlib import metadata

    (dist,) = [d for d in metadata.distributions(path=[str(target)]) if d.name == "acme-plugin"]
    assert trust.verify_signature(trust.installed_files(dist), {trust.key_id(pk): pk})


@pytest.mark.sign
def test_sign_refuses_a_damaged_wheel_and_data_folders(tmp_path, capsys):
    sk, _, _ = keypair(tmp_path)
    key = tmp_path / "k.key"
    key.write_text(base64.b64encode(sk).decode())
    wheel = build_wheel(tmp_path)
    damaged = tmp_path / "damaged.whl"
    with zipfile.ZipFile(wheel) as zin, zipfile.ZipFile(damaged, "w") as zout:
        for n in zin.namelist():
            zout.writestr(n, b"# tampered" if n == "acme_mod.py" else zin.read(n))
    assert run_cli("plugins", "sign", str(damaged), "--key", str(key)) == 2
    assert "acme_mod.py was changed" in capsys.readouterr().err
    data = build_wheel(
        tmp_path / "d" if (tmp_path / "d").mkdir() is None else tmp_path,
        extra_members={"acme_plugin-1.0.data/scripts/x": b"x"},
    )
    assert run_cli("plugins", "sign", str(data), "--key", str(key)) == 2
    assert ".data folder" in capsys.readouterr().err


@pytest.mark.sign
def test_verify_usage_errors(tmp_path, monkeypatch, capsys):
    _, _, pub = keypair(tmp_path)
    monkeypatch.delenv(trust.ENV_ALLOWLIST, raising=False)
    assert run_cli("plugins", "verify", "no-such-distribution-xyz", "--key", str(pub)) == 2
    assert run_cli("plugins", "verify", str(build_wheel(tmp_path))) == 2  # no key anywhere
    assert "no key to check against" in capsys.readouterr().err
    junk = tmp_path / "junk.whl"
    junk.write_text("not a zip")
    assert run_cli("plugins", "verify", str(junk), "--key", str(pub)) == 2
    assert (
        run_cli("plugins", "verify", str(build_wheel(tmp_path)), "--key", str(tmp_path / "x")) == 2
    )


@pytest.mark.sign
def test_verify_uses_the_trusted_keys_of_the_active_allowlist(tmp_path, monkeypatch):
    sk, pk, pub = keypair(tmp_path)
    wheel = build_wheel(tmp_path)
    trust.sign_wheel(wheel, sk)
    monkeypatch.setenv(trust.ENV_ALLOWLIST, str(signed_allowlist(tmp_path, pub, pk)))
    assert run_cli("plugins", "verify", str(wheel)) == 0


# -- 6. visibility ------------------------------------------------------------------------


def test_list_shows_allowed_and_blocked_without_importing(site, tmp_path, monkeypatch, capsys):
    put(site, tmp_path)
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "other-plugin"}])
    from shape.plugins import cli as plugin_cli

    host = host_for(site, al)
    rows = plugin_cli.list_report(host)
    (row,) = rows
    assert row["allowed"] is False and "not on the plugin allow-list" in row["reason"]
    assert "blocked --" in plugin_cli.format_list(rows)
    assert "acme_mod" not in sys.modules
    ok = host_for(site, write_allowlist(tmp_path / "b.json", [{"distribution": "acme-plugin"}]))
    (row,) = plugin_cli.list_report(ok)
    assert row["allowed"] is True and row["reason"] is None
    assert "allowed" in plugin_cli.format_list([row])
    # Without an allow-list the rows are unchanged.
    (plain,) = plugin_cli.list_report(host_for(site, None))
    assert "allowed" not in plain


def test_list_json_through_the_cli(site, tmp_path):
    put(site, tmp_path)
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "other-plugin"}])
    r = shape(site, al, "plugins", "list", "--json", "--group", "shape.detectors")
    assert r.returncode == 0, r.stderr
    rows = json.loads(r.stdout)
    assert [
        (x["name"], x["allowed"], x["status"]) for x in rows if x["source"] == "acme-plugin"
    ] == [("one", False, "blocked")]


def test_doctor_separates_blocked_and_exit_codes(site, tmp_path):
    from shape.plugins.doctor import diagnose, format_report

    put(site, tmp_path)
    # Merely not listed: reported, not an error.
    al = write_allowlist(tmp_path / "a.json", [{"distribution": "other-plugin"}])
    report = diagnose(host_for(site, al))
    assert (
        report["ok"] and report["plugins"] == [] and report["blocked"][0]["kind"] == "not_permitted"
    )
    assert "blocked by the plugin allow-list" in format_report(report)
    r = shape(site, al, "plugins", "doctor")
    assert r.returncode == 0, r.stdout + r.stderr
    # Listed but failing its version: an error.
    al2 = write_allowlist(tmp_path / "b.json", [{"distribution": "acme-plugin", "version": "==9"}])
    assert not diagnose(host_for(site, al2))["ok"]
    assert shape(site, al2, "plugins", "doctor").returncode == 1
    # Listed but its files changed: an error.
    info = site / "acme_plugin-1.0.dist-info"
    al3 = write_allowlist(
        tmp_path / "c.json", [{"distribution": "acme-plugin", "record_sha256": record_hash(info)}]
    )
    assert diagnose(host_for(site, al3))["ok"]
    (site / "acme_mod.py").write_text("# edited\n")
    assert not diagnose(host_for(site, al3))["ok"]
    j = json.loads(shape(site, al3, "plugins", "doctor", "--json").stdout)
    # Other installed distributions (shape-dbt, shape-domains) are blocked too, as not listed.
    acme = [b for b in j["blocked"] if b["source"] == "acme-plugin"]
    assert j["ok"] is False and acme and all(b["kind"] == "check_failed" for b in acme)


def test_allowlist_init_round_trip(site, tmp_path):
    info = put(site, tmp_path)
    out = tmp_path / "list.json"
    r = shape(site, None, "plugins", "allowlist", "init", "-o", str(out), "--json")
    assert r.returncode == 0, r.stderr
    summary = json.loads(r.stdout)
    assert summary["path"] == str(out) and summary["plugins"] >= 1
    doc = json.loads(out.read_text())
    assert doc["format"] == "shape-plugin-allowlist" and doc["version"] == 1
    mine = [p for p in doc["plugins"] if p["distribution"] == "acme-plugin"]
    assert mine == [{"distribution": "acme-plugin", "version": "==1.0", "names": ["detectors:one"]}]
    assert all(p["distribution"] != "sqllocks-shape" for p in doc["plugins"])
    assert "acme_mod" not in out.read_text()
    # Never overwrites.
    again = shape(site, None, "plugins", "allowlist", "init", "-o", str(out))
    assert again.returncode == 2 and "already exists" in again.stderr
    assert json.loads(out.read_text()) == doc
    # What it wrote allows what is installed, and imports nothing to do it.
    r = shape(site, out, "plugins", "doctor")
    assert r.returncode == 0 and "blocked" not in r.stdout
    assert "ok    shape.detectors:one [acme-plugin]" in r.stdout
    # With --pin-hashes the RECORD hash is pinned.
    pinned = tmp_path / "pinned.json"
    assert (
        shape(
            site, None, "plugins", "allowlist", "init", "-o", str(pinned), "--pin-hashes"
        ).returncode
        == 0
    )
    (entry,) = [
        p for p in json.loads(pinned.read_text())["plugins"] if p["distribution"] == "acme-plugin"
    ]
    assert entry["record_sha256"] == record_hash(info)
    assert trust.load_allowlist(pinned)


def test_allowlist_init_yaml_and_default_name(site, tmp_path):
    pytest.importorskip("yaml")
    put(site, tmp_path)
    out = tmp_path / "list.yml"
    assert shape(site, None, "plugins", "allowlist", "init", "-o", str(out)).returncode == 0
    assert trust.load_allowlist(out).plugins["acme-plugin"].names == (("shape.detectors", "one"),)
    r = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; from shape.cli.main import main; sys.exit(main())",
            "plugins",
            "allowlist",
            "init",
        ],
        capture_output=True,
        text=True,
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": str(site)},
    )
    assert r.returncode == 0 and (tmp_path / "shape-plugin-allowlist.json").is_file()
