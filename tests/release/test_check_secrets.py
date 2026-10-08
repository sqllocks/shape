"""scripts/check_secrets.py: what it reports, and what it must not (#262, #350).

The tests up to ``_repo`` came with AUD-ci (#262) and run the script on a scratch tree that is not
a git checkout; the rest came with AUD-docs (#262, #350).

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


B64_CI = "Q2hlY2tTZWNyZXRzVGVzdEZpeHR1cmVOb3RBUmVhbEtleQ"  # synthetic, not a credential


def _run(tmp_path: Path, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    (tmp_path / "scripts").mkdir(exist_ok=True)
    shutil.copy(ROOT / "scripts" / "check_secrets.py", tmp_path / "scripts" / "check_secrets.py")
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(tmp_path / "scripts" / "check_secrets.py")],
        capture_output=True,
        text=True,
        check=False,
    )


def test_clean_tree_passes(tmp_path):
    r = _run(tmp_path, {"src/a.py": "x = 1\n"})
    assert r.returncode == 0, r.stdout


@pytest.mark.parametrize(
    "text",
    [
        "-----BEGIN RSA " + "PRIVATE KEY-----\n",  # split: this file must not match itself
        "api_key" + ' = "abcdefghijklmnop"\n',
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\n" + B64_CI + "\n",
        "-----BEGIN DSA PRIVATE KEY-----\n" + B64_CI + "\n",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n" + B64_CI + "\n",
        "conn = 'DefaultEndpointsProtocol=https;AccountName=a;AccountKey=" + B64_CI + "=='\n",
        "c = 'Endpoint=sb://x.servicebus.windows.net/;SharedAccessKeyName=R;SharedAccessKey="
        + B64_CI[:43]
        + "='\n",
        "t = 'ghp_" + "A1b2C3d4" * 5 + "'\n",
        "t = 'github_pat_" + "A1b2C3d4_" * 9 + "'\n",
        "k = 'AKIA" + "ABCDEFGHIJ234567" + "'\n",
        'client_secret = "' + "s3cr3tValue" * 3 + '"\n',
    ],
)
def test_secret_is_reported(tmp_path, text):
    r = _run(tmp_path, {"src/leak.py": text})
    assert r.returncode == 1 and "src/leak.py" in r.stdout, (text, r.stdout)


def test_marker_strings_without_key_material_pass(tmp_path):
    # code that recognises a key format names its header; that is not a key
    code = 'ENC = "-----BEGIN ENCRYPTED PRIVATE KEY-----" in text\nSAS = "SharedAccessKey="\n'
    r = _run(tmp_path, {"src/keys.py": code})
    assert r.returncode == 0, r.stdout


def test_nosec_marks_a_deliberate_fake_on_its_line_only(tmp_path):
    fake = 'client_secret="fake-client-secret-for-tests",  # nosec B106\n'
    assert _run(tmp_path, {"src/a.py": fake}).returncode == 0
    leak = "client_secret" + '="fake-client-secret-for-tests",\n# nosec on another line\n'
    assert _run(tmp_path, {"src/a.py": leak}).returncode == 1
    # the original two patterns take no exemption
    old = "api_key" + ' = "abcdefghijklmnop"  # nosec\n'
    assert _run(tmp_path, {"src/a.py": old}).returncode == 1


def test_security_test_fixtures_and_key_files(tmp_path):
    fixture = "k = 'AKIA" + "ABCDEFGHIJ234567" + "'\n"
    assert _run(tmp_path, {"tests/security/test_x.py": fixture}).returncode == 0
    r = _run(tmp_path, {"deploy/server.pem": "x"})
    assert r.returncode == 1 and "deploy/server.pem" in r.stdout


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
    "encrypted private key": "-----BEGIN " + "ENCRYPTED PRIVATE KEY-----\n" + B64,
    "dsa private key": "-----BEGIN " + "DSA PRIVATE KEY-----\n" + B64,
    "key in a json string": '{"key": "-----BEGIN ' + "ENCRYPTED PRIVATE KEY-----\\n" + B64 + '"}',
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


def test_code_that_names_a_secret_is_not_a_secret(tmp_path):
    text = (
        'ENCRYPTED = "-----BEGIN ' + 'ENCRYPTED PRIVATE KEY-----" in text\n'
        'raise RuntimeError("detail client' + '_secret=" + SECRET)\n'
        "fake = Credential(client" + '_secret="fake-client-secret-for-tests")  # nosec B106\n'
    )
    root = _repo(tmp_path, {"src/m.py": text})
    r = _scan(root)
    assert r.returncode == 0, r.stdout


def test_findings_name_the_line(tmp_path):
    root = _repo(tmp_path, {"src/m.py": "x = 1\n" + SECRETS["aws key id"] + "\n"})
    assert "src/m.py:2" in _scan(root).stdout


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


def test_form_feeds_and_lone_carriage_returns_do_not_break_line_numbers(tmp_path):
    text = "a\x0cb\rc\x1cd\n" * 3 + SECRETS["aws key id"] + "\n"
    root = _repo(tmp_path, {"src/m.py": text})
    assert "src/m.py:4" in _scan(root).stdout
