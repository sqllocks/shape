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
        [sys.executable, "-m", "shape.cli.main", "git-setup", "--repo", str(repo), "--command", "x", *args],
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


# ---- local registry --------------------------------------------------------------------------------


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
            [sys.executable, "-m", "shape.cli.main", "show", str(p), "--verify",
             str(tmp_path / "trusted.pub")],
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
