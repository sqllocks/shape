"""scripts/check_secrets.py: what it reports, and what it must not (#262, #350).

The script is copied into a scratch repository and run there, so the fixtures below are the
only files it sees. Secret-shaped fixtures are assembled at run time: this file itself must not
match the scanner.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
B64 = "Zm9vYmFyYmF6cXV4" * 3 + "AB=="  # 52 base64 characters


def _repo(tmp_path: Path, files: dict[str, str], *, git: bool = True) -> Path:
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check_secrets.py", tmp_path / "scripts" / "check_secrets.py")
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, "utf-8")
    if git:
        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    return tmp_path


def _scan(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "check_secrets.py")],
        capture_output=True,
        text=True,
        check=False,
    )


SECRETS = {
    "storage account key": "conn = 'DefaultEndpointsProtocol=https;AccountName=a;"
    + "AccountKey="
    + B64
    + "'",
    "event hubs key": "Endpoint=sb://x.servicebus.windows.net/;SharedAccessKeyName=R;"
    + "SharedAccessKey="
    + B64,
    "github token": "token = " + "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "github fine-grained token": "github"
    + "_pat_"
    + "11ABCDEFG0123456789_abcdefghijklmnopqrstuvwxyz",
    "aws key id": "aws = " + "AK" + "IA" + "ABCDEFGHIJKLMNOP",
    "client secret": "client" + "_secret = '" + "s3cr3t-v4lue-0123456789" + "'",
    "encrypted private key": "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----",
    "dsa private key": "-----BEGIN " + "DSA PRIVATE KEY-----",
    "rsa private key": "-----BEGIN " + "RSA PRIVATE KEY-----",
    "api key": "api" + "_key = '" + "0123456789abcdef" + "'",
}


@pytest.mark.parametrize("kind", sorted(SECRETS))
def test_each_credential_form_is_reported(tmp_path, kind):
    """#262: the credentials Shape handles (storage, Event Hubs, GitHub, AWS, Entra) are found."""
    root = _repo(tmp_path, {"src/m.py": SECRETS[kind] + "\n"})
    r = _scan(root)
    assert r.returncode == 1, (kind, r.stdout)
    assert "src/m.py" in r.stdout


def test_placeholders_in_docs_are_not_secrets(tmp_path):
    text = (
        "Set `AccountKey=<key>` or `SharedAccessKey=...`; a token looks like `" + "gh" + "p_...`.\n"
        "client" + "_secret = os.environ['CLIENT_SECRET']\n"
    )
    root = _repo(tmp_path, {"docs/x.md": text})
    r = _scan(root)
    assert r.returncode == 0, r.stdout


def test_a_key_file_is_reported(tmp_path):
    root = _repo(tmp_path, {"deploy/server.pem": "x"})
    r = _scan(root)
    assert r.returncode == 1 and "deploy/server.pem" in r.stdout


def test_an_in_tree_virtualenv_is_not_scanned(tmp_path):
    """#350: `python -m venv venv` holds pip's cacert.pem; git ignores it, so does the scan."""
    root = _repo(
        tmp_path,
        {
            ".gitignore": "venv/\n",
            "venv/lib/python3.11/site-packages/pip/_vendor/certifi/cacert.pem": "x",
            "src/ok.py": "x = 1\n",
        },
    )
    r = _scan(root)
    assert r.returncode == 0, r.stdout


def test_git_ignored_files_are_not_scanned(tmp_path):
    root = _repo(tmp_path, {".gitignore": ".env\n", ".env": SECRETS["aws key id"] + "\n"})
    r = _scan(root)
    assert r.returncode == 0, r.stdout


def test_untracked_files_that_are_not_ignored_are_scanned(tmp_path):
    root = _repo(tmp_path, {"new.py": SECRETS["aws key id"] + "\n"})
    r = _scan(root)
    assert r.returncode == 1 and "new.py" in r.stdout


def test_outside_a_git_checkout_every_file_is_scanned(tmp_path):
    root = _repo(tmp_path, {"src/m.py": SECRETS["aws key id"] + "\n"}, git=False)
    r = _scan(root)
    assert r.returncode == 1 and "src/m.py" in r.stdout


def test_security_test_fixtures_are_exempt(tmp_path):
    root = _repo(tmp_path, {"tests/security/test_x.py": SECRETS["aws key id"] + "\n"})
    r = _scan(root)
    assert r.returncode == 0, r.stdout


def test_the_repository_passes():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_secrets.py")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stdout
