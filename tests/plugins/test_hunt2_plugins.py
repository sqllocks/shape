"""HUNT2-plugins: regressions for the trust, signing, CLI and kit defects found in the second
audit pass (issues #579-#588)."""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import math
import os
import stat
import sys

import pyarrow as pa
import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_trust import (  # noqa: E402
    build_wheel,
    host_for,
    keypair,
    put,
    site,  # noqa: F401  (fixture)
    write_allowlist,
)

from shape.plugins import cli as plugin_cli
from shape.plugins import kit, trust
from shape.plugins.api import v1
from shape.plugins.host import PluginHost

pytestmark = pytest.mark.contract


def _b64(digest: bytes) -> str:
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


class _Files(trust.DistFiles):
    info_dir = "x-1.dist-info"

    def __init__(self, rows: list[list[str]]) -> None:
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        self._record = buf.getvalue().encode()

    def read(self, path: str) -> bytes:
        return self._record


# -- #579 ---------------------------------------------------------------------------------


def test_signed_list_does_not_collide_through_a_line_break():
    d1, d2 = hashlib.sha256(b"X").digest(), hashlib.sha256(b"Y").digest()
    plain = _Files([["x", _b64(d1), "1"], ["y", _b64(d2), "1"]])
    assert trust._signed_list(plain) == f"x {d1.hex()}\ny {d2.hex()}\n".encode()
    forged = _Files([[f"x {d1.hex()}\ny", _b64(d2), "1"]])
    with pytest.raises(trust.PluginTrustError, match="line break"):
        trust._signed_list(forged)


@pytest.mark.parametrize("bad", ["a\nb", "a\rb", "\n", "a b\r\nc"])
def test_signed_list_refuses_any_line_break(bad):
    files = _Files([[bad, _b64(hashlib.sha256(b"X").digest()), "1"]])
    with pytest.raises(trust.PluginTrustError, match="line break"):
        trust._signed_list(files)


def test_signed_list_still_allows_spaces_and_unicode():
    digest = hashlib.sha256(b"X").digest()
    files = _Files([["dir/a b é.py", _b64(digest), "1"]])
    assert trust._signed_list(files) == f"dir/a b é.py {digest.hex()}\n".encode()


# -- #580 ---------------------------------------------------------------------------------


def test_parse_record_wraps_csv_errors():
    with pytest.raises(trust.PluginTrustError, match="RECORD"):
        trust.parse_record(b"a.py,sha256=" + b"A" * 200_000 + b",1\n")


def test_verify_files_refuses_variable_length_hash():
    class F(_Files):
        def read(self, path: str) -> bytes:
            return b"a.py,shake_128=AAAA,1\n" if path.endswith("RECORD") else b"x"

    with pytest.raises(trust.PluginTrustError, match="shake_128"):
        trust.verify_files(F([]))


@pytest.mark.parametrize(
    "tail", [b"zzz.py,sha256=" + b"A" * 200_000 + b",1\n", b"acme_mod.py,shake_128=AAAA,1\n"]
)
def test_bad_record_blocks_the_plugin_instead_of_escaping_discovery(site, tmp_path, tail):  # noqa: F811
    info = put(site, tmp_path)
    with open(info / "RECORD", "ab") as fh:
        fh.write(tail)
    sha = hashlib.sha256((info / "RECORD").read_bytes()).hexdigest()
    al = write_allowlist(
        tmp_path / "a.json", [{"distribution": "acme-plugin", "record_sha256": sha}]
    )
    rec = host_for(site, al).record("shape.detectors", "one")
    assert rec is not None and rec.status == "blocked"
    assert "acme_mod" not in sys.modules


# -- #581, #582, #588 ---------------------------------------------------------------------


def test_sign_wheel_keeps_a_comma_in_a_file_name(tmp_path):
    sk, pk, _ = keypair(tmp_path)
    wheel = build_wheel(tmp_path, extra_members={"data,file.txt": b"hello", 'q"uote.txt': b"q"})
    trust.sign_wheel(wheel, sk)
    archive, files = trust.open_wheel(wheel)
    try:
        assert trust.verify_signature(files, {trust.key_id(pk): pk}) == trust.key_id(pk)
        rows = {r.path for r in trust.parse_record(files.record_bytes())}
    finally:
        archive.close()
    assert {"data,file.txt", 'q"uote.txt'} <= rows


@pytest.mark.skipif(os.name == "nt", reason="mode bits")
@pytest.mark.parametrize("to_out", [False, True])
def test_sign_wheel_keeps_the_file_mode(tmp_path, to_out):
    sk, _pk, _ = keypair(tmp_path)
    wheel = build_wheel(tmp_path)
    wheel.chmod(0o644)
    out = tmp_path / "signed.whl" if to_out else None
    trust.sign_wheel(wheel, sk, out)
    assert stat.S_IMODE((out or wheel).stat().st_mode) == 0o644


def test_sign_wheel_error_names_the_output_path(tmp_path):
    sk, _pk, _ = keypair(tmp_path)
    wheel = build_wheel(tmp_path)
    missing = tmp_path / "nope" / "out.whl"
    with pytest.raises(FileNotFoundError) as info:
        trust.sign_wheel(wheel, sk, missing)
    assert info.value.filename == str(missing)


@pytest.mark.parametrize("version", [0, -3])
def test_signature_version_below_one_is_refused(version):
    import json

    doc = {
        "format": trust.SIGNATURE_FORMAT,
        "version": version,
        "algorithm": trust.ALGORITHM,
        "key_id": "k",
        "signature": base64.b64encode(b"s").decode(),
    }
    with pytest.raises(trust.PluginTrustError, match="version"):
        trust._parse_signature(json.dumps(doc).encode(), "sig")


# -- #584, #585 ---------------------------------------------------------------------------


def test_kit_treats_nan_as_equal_to_itself():
    class Strat:
        name = "nanstrat"

        def generate(self, spec, ctx):
            return pa.array([math.nan if i % 2 else 1.0 for i in range(ctx.n_rows)])

    kit.check_strategy(Strat(), {})
    assert not kit._same(pa.array([1.0, 2.0]), pa.array([1.0, math.nan]))


def test_kit_source_with_nan_data_is_deterministic():
    class Src:
        name = "nansrc"
        schemes = ("file",)

        def can_open(self, uri):
            return uri.startswith("file")

        def schema(self, uri, **options):
            return pa.schema([("a", pa.float64())])

        def read(self, uri, **options):
            yield pa.record_batch([pa.array([math.nan, 1.0])], schema=self.schema(uri))

    kit.check_source(Src(), "file:x")


def _behavior(entity_ids, kinds):
    import datetime

    class Beh:
        name = "b"
        version = "1"
        states = ("s",)
        attributes = ()
        events = ("e",)

        def simulate(self, population, seed, years):
            n = len(entity_ids)
            return pa.table(
                {
                    "entity_id": pa.array(entity_ids, pa.int64()),
                    "time": pa.array([datetime.datetime(2020, 1, 1)] * n, pa.timestamp("us")),
                    "state": ["s"] * n,
                    "kind": pa.array(kinds, pa.string()),
                }
            )

    return Beh()


def test_check_behavior_reports_null_entity_id_as_conformance_error():
    with pytest.raises(kit.ConformanceError, match="entity_id"):
        kit.check_behavior(_behavior([0, None], ["e", "e"]))
    with pytest.raises(kit.ConformanceError, match="kind"):
        kit.check_behavior(_behavior([0, 1], ["e", None]))
    kit.check_behavior(_behavior([0, 1], ["e", "e"]))  # negative case: a good one passes


def test_check_chaos_rejects_bool_rows_affected():
    class Chaos:
        name = "c"

        def mutate(self, batch, seed):
            return batch, v1.ChaosReport("c", True)

    with pytest.raises(kit.ConformanceError, match="rows_affected"):
        kit.check_chaos(Chaos(), pa.record_batch([pa.array([1])], names=["a"]))


# -- #586, #587 ---------------------------------------------------------------------------


class _Boom:
    name = "boom"
    help = "crashes"

    def configure(self, parser):
        pass

    def run(self, args):
        raise RuntimeError("kaboom")


def _host():
    host = PluginHost(entry_points=lambda: [])
    host.register("shape.commands", "boom", _Boom)
    return host


def test_run_command_crash_shows_the_debug_hint(capsys):
    from shape.cli import errors

    assert not errors.debug_enabled()
    assert plugin_cli.run_command(_host(), "boom", []) == 1
    assert "--debug" in capsys.readouterr().err


def test_run_command_crash_propagates_under_debug(monkeypatch):
    monkeypatch.setenv("SHAPE_DEBUG", "1")
    with pytest.raises(RuntimeError, match="kaboom"):
        plugin_cli.run_command(_host(), "boom", [])


def test_short_group_names_resolve():
    host = _host()
    assert [r.name for r in plugin_cli.find_records(host, "commands:boom")] == ["boom"]
    assert [r.name for r in plugin_cli.find_records(host, "shape.commands:boom")] == ["boom"]
    assert plugin_cli.find_records(host, "nogroup:boom") == []
    assert [r["name"] for r in plugin_cli.list_report(host, "commands")] == ["boom"]
    assert plugin_cli.list_report(host, "nogroup") == []
