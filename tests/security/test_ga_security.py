import json
import zipfile

import pytest

from shape.artifact import write_shape
from shape.artifact.io import ArtifactError, read_artifact
from shape.privacy import release_for
from shape.query import ShapeQueryError, query
from shape.security import SecurityError, require_no_downgrade, scan_secrets, validate_structure


def test_structure_depth_bomb():
    x = {}
    r = x
    for _i in range(70):
        r["x"] = {}
        r = r["x"]
    with pytest.raises(SecurityError):
        validate_structure(x)


def test_secret_detection_all_patterns():
    for x in [
        "AK" + "IA" + "ABCDEFGHIJKLMNOP",
        "-----BEGIN " + "PRIVATE KEY-----",
        "ghp_" + "a" * 40,
        "Endpoint=" + "sb://x/;" + "SharedAccessKeyName=n",
    ]:
        assert scan_secrets({"x": x})


def test_artifact_refuses_secret(tmp_path):
    with pytest.raises(SecurityError):
        write_shape(tmp_path / "x.shape", {"token": "ghp_" + "a" * 40})


def test_classification_downgrade_denied():
    assert require_no_downgrade("PII", "SECRET")
    with pytest.raises(SecurityError):
        require_no_downgrade("TOP_SECRET", "PUBLIC")


def test_sanitized_derivative_provenance():
    s = {
        "rows": 100,
        "columns": {"email": {"kind": "text", "count": 100, "topk": [["person@example.com", 5]]}},
    }
    r = release_for(s, {"email": "PII"}, "PUBLIC", source_classification="TOP_SECRET")
    assert r.allowed is False and r.reason == "source_exceeds_target"
    assert "person@example.com" not in str(r.shape)


def test_query_injection_matrix():
    s = {"rows": 1, "columns": {"x": {"kind": "numeric"}}}
    for q in [
        '__import__("os")',
        'column("x").__class__',
        'rows;system("id")',
        'column("../x")',
        'relationship("a","b");x',
    ]:
        try:
            result = query(s, q)
            assert result is None, f"unsafe query resolved data: {q}"
        except ShapeQueryError:
            pass


def _zip(path, members):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in members:
            z.writestr(n, b)


def test_archive_path_traversal(tmp_path):
    p = tmp_path / "x.zip"
    _zip(p, [("manifest.json", b'{"content_hashes":{}}'), ("../evil", b"x")])
    with pytest.raises(ArtifactError):
        read_artifact(p)


def test_archive_duplicate_member(tmp_path):
    p = tmp_path / "x.zip"
    _zip(p, [("manifest.json", b'{"content_hashes":{}}'), ("manifest.json", b"{}")])
    with pytest.raises(ArtifactError):
        read_artifact(p)


def test_archive_unexpected_member(tmp_path):
    p = tmp_path / "x.zip"
    _zip(p, [("manifest.json", b'{"content_hashes":{}}'), ("evil", b"x")])
    with pytest.raises(ArtifactError):
        read_artifact(p)


def test_archive_checksum_tamper(tmp_path):
    p = tmp_path / "x.zip"
    _zip(p, [("manifest.json", b'{"content_hashes":{"shape.json":"00"}}'), ("shape.json", b"{}")])
    with pytest.raises(ArtifactError):
        read_artifact(p)


def test_archive_ratio_bomb(tmp_path):
    p = tmp_path / "x.zip"
    payload = b"0" * 2_000_000
    import hashlib

    m = json.dumps({"content_hashes": {"shape.json": hashlib.sha256(payload).hexdigest()}}).encode()
    _zip(p, [("manifest.json", m), ("shape.json", payload)])
    with pytest.raises(ArtifactError):
        read_artifact(p, max_ratio=10)
