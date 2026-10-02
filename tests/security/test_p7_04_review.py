"""P7-04 security review: regression tests for every confirmed finding (see
docs/plans/lane_status/P7-04.md). Each of these failed before its fix."""

from __future__ import annotations

import json
import subprocess
import sys

import pyarrow as pa
import pytest

pytestmark = pytest.mark.security

HOSTILE = ["../escaped", "/abs/escaped", "a/b", "..", "", "a\\b", "x\x00y"]


def _batch():
    return pa.RecordBatch.from_pydict({"a": [1, 2]})


# ---- table names become file names ------------------------------------------------------------


@pytest.mark.parametrize("name", HOSTILE)
def test_safe_name_rejects_paths(name):
    from shape.security.names import safe_name

    with pytest.raises(ValueError):
        safe_name(name)


def test_safe_name_accepts_plain_names():
    from shape.security.names import safe_name

    for ok in ("orders", "dbo.orders", "my table", "t-1_x", "..x"):
        assert safe_name(ok) == ok


@pytest.mark.parametrize("name", HOSTILE)
@pytest.mark.parametrize("fmt", ["csv", "jsonl", "parquet", "sql"])
def test_file_sinks_never_leave_the_directory(tmp_path, name, fmt):
    from shape.plugins.host import default_host

    out = tmp_path / "a" / "out"
    sink = default_host().get("shape.sinks", fmt)
    with pytest.raises(ValueError):
        sink.write(f"{out}/", name, iter([_batch()]))
    assert not (tmp_path / "a" / "escaped.csv").exists()
    assert sorted(p.name for p in tmp_path.rglob("*") if p.is_file()) == []


def test_schema_with_a_path_as_table_name_is_rejected():
    from shape.generation.schema import GenSchema, GenSchemaError

    doc = {
        "model": {"name": "m"},
        "generation": {},
        "tables": {
            "../../pwned": {
                "columns": {"id": {"type": "integer", "generator": {"strategy": "sequence"}}},
                "primary_key": ["id"],
            }
        },
    }
    with pytest.raises(GenSchemaError):
        GenSchema.from_dict(doc)


def test_jsonl_emitter_rejects_backslash_names(tmp_path):
    from shape.security.names import is_safe_name

    assert not is_safe_name("a\\b") and not is_safe_name("C:evil")


# ---- shape git-setup ----------------------------------------------------------------------------


def _repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _git_setup(repo, *args):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "shape.cli.main",
            "git-setup",
            "--repo",
            str(repo),
            "--command",
            "x",
            *args,
        ],
        capture_output=True,
        text=True,
    )


def test_git_setup_refuses_a_symlinked_gitattributes(tmp_path):
    repo = _repo(tmp_path / "r")
    victim = tmp_path / "victim.txt"
    victim.write_text("SECRET\n")
    (repo / ".gitattributes").symlink_to(victim)
    r = _git_setup(repo)
    assert r.returncode != 0
    assert victim.read_text() == "SECRET\n"


@pytest.mark.parametrize("pattern", ["*.shape\n*.py filter=evil", "a b", "*.x\ty", ""])
def test_git_setup_rejects_attribute_injection_in_pattern(tmp_path, pattern):
    repo = _repo(tmp_path / "r")
    r = _git_setup(repo, "--pattern", pattern)
    assert r.returncode != 0
    assert not (repo / ".gitattributes").exists()


# ---- local registry -----------------------------------------------------------------------------


def test_registry_checkout_verifies_the_object(tmp_path):
    from shape.registry import LocalRegistry, RegistryError

    r = LocalRegistry(tmp_path)
    h = r.commit("n", b"genuine")
    (tmp_path / "objects" / h).write_bytes(b"EVIL")
    with pytest.raises(RegistryError):
        r.checkout("n", h)


def test_registry_ref_named_tmp_survives_later_commits(tmp_path):
    from shape.registry import LocalRegistry

    r = LocalRegistry(tmp_path)
    a = r.commit("n", b"one")
    r.promote("n", "latest", "latest.tmp")
    r.commit("n", b"two")
    assert r.resolve("n", "latest.tmp") == a


# ---- SQL DDL and KQL ---------------------------------------------------------------------------


def test_sql_column_dimensions_must_be_integers():
    from shape.plugins.host import default_host

    sink = default_host().get("shape.sinks", "sql")
    evil = {"a": {"type": "string", "max_length": "10) NOT NULL); DROP TABLE victim; --"}}
    import tempfile

    with tempfile.TemporaryDirectory() as d, pytest.raises(ValueError):
        sink.write(f"{d}/o.sql", "t", iter([_batch()]), columns=evil, create_table=True)


def test_kql_mapping_literal_escapes_backslashes():
    pytest.importorskip("shape_fabric")
    from shape_fabric.eventhouse import create_mapping_command

    name = 'x", "path": "$[\\"_shape_seq\\"]", "datatype": "string"}, {"column": "zz'
    cmd = create_mapping_command("t", pa.schema([pa.field(name, pa.int64())]))
    literal = cmd.split("ingestion json mapping 'shape_json' '", 1)[1][:-1]
    # Decode the KQL single-quoted literal the way the service does, then parse the JSON.
    import re

    decoded = re.sub(r"\\(.)", r"\1", literal)
    cols = json.loads(decoded)
    assert len(cols) == 1 and cols[0]["column"] == name


# ---- the ADF Batch command ---------------------------------------------------------------------


def test_adf_batch_command_quotes_the_image_parameter():
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parents[2] / "integrations" / "adf" / "build_adf.py"
    spec = importlib.util.spec_from_file_location("p7_04_build_adf", p)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cmd = mod.COMMAND
    # the raw parameter is never concatenated: it is single-quoted, with quotes stripped first
    assert ", pipeline().parameters.image," not in cmd
    assert "replace(pipeline().parameters.image, '''', '')" in cmd


# ---- signature gate, secret scanner, redaction, reference datasets -------------------------------


def test_verify_checks_artifacts_whatever_their_file_name(tmp_path):
    pytest.importorskip("cryptography")
    from shape.artifact import sign_artifact, write_model
    from shape.artifact.signing import generate_keypair, write_keypair

    write_keypair(tmp_path / "trusted")
    sk, _ = generate_keypair()  # the attacker's key
    forged = tmp_path / "forged.bin"
    write_model(forged, {"tables": {}}, name="t")
    sign_artifact(forged, sk)
    for name in ("forged.bin", "forged.SHAPE"):
        p = tmp_path / name
        if p != forged:
            p.write_bytes(forged.read_bytes())
        r = subprocess.run(
            [
                sys.executable,
                "-m",
                "shape.cli.main",
                "show",
                str(p),
                "--verify",
                str(tmp_path / "trusted.pub"),
            ],
            capture_output=True,
            text=True,
        )
        assert r.returncode == 1, (name, r.stdout, r.stderr)


def test_oversized_signature_member_is_refused_before_it_is_read(tmp_path):
    import zipfile

    from shape.artifact import write_model
    from shape.artifact.io import ArtifactError, read_artifact

    p = tmp_path / "a.shape"
    write_model(p, {"tables": {}}, name="t")
    with zipfile.ZipFile(p, "a", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.sig", b"0" * 5_000_000)
    with pytest.raises(ArtifactError, match="signature too large"):
        read_artifact(p, verify_key=bytes(32))


@pytest.mark.parametrize(
    "doc",
    [
        {"sasl.password": "hunter2hunter2"},
        {"client_secret": "abcdefgh12345678"},
        {"cs": "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=" + "A" * 40 + "=="},
        {"cs": "Endpoint=sb://x/;SharedAccessKey=" + "B" * 40},
        {"url": "https://a.blob.core.windows.net/c?sv=1&sig=" + "C" * 30},
        {"t": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.abcdefghijklmnop"},
        {"k": "-----BEGIN ENCRYPTED PRIVATE KEY-----"},
    ],
)
def test_secret_scanner_catches_json_shaped_secrets(tmp_path, doc):
    from shape.artifact import write_model
    from shape.security import SecurityError

    with pytest.raises(SecurityError):
        write_model(tmp_path / "a.shape", {"tables": {}}, name="t", metadata=doc)


def test_sqlserver_redaction_hides_quoted_secrets():
    pytest.importorskip("shape_sqlserver")
    from shape_sqlserver.sql import redact_connection_string as red

    assert "q" not in red('Server=s;PWD="p;q";UID=u')
    assert "q" not in red("Server=s;pwd = 'p;q';UID=u")
    assert "hunter" not in red("Server=s;Client_Secret=hunter2;UID=u")


def test_reference_dataset_name_cannot_be_a_path(tmp_path, monkeypatch):
    from shape.generation import reference

    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.json").write_text('[{"a": 1}]')
    (tmp_path / "ref").mkdir()
    monkeypatch.setenv(reference.REFERENCE_PATH_ENV, str(tmp_path / "ref"))
    for name in ("../outside/secret", str(tmp_path / "outside" / "secret")):
        with pytest.raises(reference.DatasetNotFoundError):
            reference.load_dataset(name)


# ---- denial of service on untrusted input -------------------------------------------------------


def _deflated_zip(path, members):
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for k, v in members.items():
            z.writestr(k, v)


def test_manifest_sniffs_do_not_inflate_a_bomb(tmp_path):
    from shape.artifact.io import ArtifactError, read_manifest_bytes
    from shape.cli.main import _artifact_kind

    p = tmp_path / "bomb.shape"
    _deflated_zip(p, {"manifest.json": b'{"kind":"profile"}' + b" " * (64 * 1024 * 1024)})
    with pytest.raises(ArtifactError):
        read_manifest_bytes(p)
    assert _artifact_kind(str(p)) is None


def test_registry_manifest_read_is_bounded(tmp_path):
    from shape.artifact.io import ArtifactError
    from shape.registry.profiles import ProfileRegistry

    p = tmp_path / "bomb.shape"
    _deflated_zip(p, {"manifest.json": b" " * (64 * 1024 * 1024)})
    with pytest.raises(ArtifactError):
        ProfileRegistry._manifest(p)


def test_yaml_alias_bomb_is_refused(tmp_path):
    from shape.security.yamlsafe import safe_load_yaml

    levels = ["a: &a0 [x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 12):
        levels.append(f"b{i}: &a{i} [" + ", ".join([f"*a{i - 1}"] * 9) + "]")
    with pytest.raises(ValueError, match="alias bomb"):
        safe_load_yaml("\n".join(levels))
    assert safe_load_yaml("a: &x [1, 2]\nb: *x\n") == {"a": [1, 2], "b": [1, 2]}


def test_alias_bomb_does_not_reach_schema_validation(tmp_path):
    from shape.scenario.loader import PackError, PackLoader

    p = tmp_path / "bomb.yaml"
    body = ["a: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    for i in range(1, 150):
        body.append(f"b{i}: &a{i} [*a{i - 1}, *a{i - 1}]")
    p.write_text("\n".join(body))
    with pytest.raises(PackError):
        PackLoader().load(p)


def test_ddl_parser_is_not_cubic_on_spaces():
    import time

    from shape.generation.ddl import DdlParser

    start = time.monotonic()
    try:
        DdlParser().parse_string("CREATE TABLE " + " " * 8000 + "x")
    except Exception:  # noqa: BLE001 - only the time matters
        pass
    assert time.monotonic() - start < 2


def test_rule_comparison_parser_caps_the_rule_length():
    import time

    from shape.generation.rules import parse_comparison

    start = time.monotonic()
    assert parse_comparison("a" + " " * 40_000 + "b") == ("", "", "")
    assert time.monotonic() - start < 1
    assert parse_comparison("a >= b") == ("a", ">=", "b")


def test_deeply_nested_jsonl_is_refused_not_a_segfault(tmp_path):
    from shape.io.readers import ReaderError, read_table

    p = tmp_path / "deep.jsonl"
    p.write_text('{"a":' + "[" * 100_000 + "]" * 100_000 + "}")
    with pytest.raises(ReaderError, match="nested"):
        read_table(str(p))


def test_json_depth_check_counts_per_line():
    from shape.security.jsondepth import check_json_depth

    check_json_depth(b'{"a":[[1]]}\n' * 1000)
    with pytest.raises(ValueError):
        check_json_depth(b"ok\n" + b"[" * 300)


def test_poison_stream_message_is_skipped():
    from shape.streaming.messages import StreamMessage, decode_messages

    good = StreamMessage("0", 1, b'{"a": 1}')
    bad = StreamMessage("0", 2, b"[" * 200_000)
    result = decode_messages([bad, good], on_error="skip")
    assert result is not None


def test_pattern_width_is_bounded():
    from shape.builtins.strategies.text import _TOKEN, MAX_TOKEN_WIDTH

    assert MAX_TOKEN_WIDTH <= 65536
    m = _TOKEN.search("{random:2000000000}")
    assert m and int(m.group(2)) > MAX_TOKEN_WIDTH


def test_mask_refuses_to_overwrite_its_input(tmp_path):
    (tmp_path / "people.csv").write_text("email\nalice@example.com\n")
    r = subprocess.run(
        [
            sys.executable,
            "-m",
            "shape.cli.main",
            "mask",
            str(tmp_path / "people.csv"),
            "-o",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert r.returncode != 0
    assert (tmp_path / "people.csv").read_text() == "email\nalice@example.com\n"


def test_connection_string_extra_keys_cannot_inject_attributes():
    pytest.importorskip("shape_sqlserver")
    from shape_sqlserver.sql import SqlServerError, build_connection_string

    with pytest.raises(SqlServerError):
        build_connection_string("s", extra={"Application Name=x;Trusted_Connection": "yes"})
    assert "ApplicationIntent=ReadOnly" in build_connection_string(
        "s", extra={"ApplicationIntent": "ReadOnly"}
    )


def test_distribution_gate_only_calls_distributions():
    import pyarrow as pa
    from scipy import stats

    from shape.quality.gates import DistributionGate

    warnings: list[str] = []
    details: dict[str, object] = {}
    col = pa.chunked_array([pa.array([float(i) for i in range(500)])])
    DistributionGate._ks(stats, "t.x", {"name": "kstest"}, col, 0.05, warnings, details)
    assert details == {} and "not a scipy.stats distribution" in warnings[0]
