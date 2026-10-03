"""scripts/check_secrets.py: run against a scratch tree (it scans the tree above its own folder)."""

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
B64 = "Q2hlY2tTZWNyZXRzVGVzdEZpeHR1cmVOb3RBUmVhbEtleQ"  # synthetic, not a credential


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
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\n" + B64 + "\n",
        "-----BEGIN DSA PRIVATE KEY-----\n" + B64 + "\n",
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\n\n" + B64 + "\n",
        "conn = 'DefaultEndpointsProtocol=https;AccountName=a;AccountKey=" + B64 + "=='\n",
        "c = 'Endpoint=sb://x.servicebus.windows.net/;SharedAccessKeyName=R;SharedAccessKey="
        + B64[:43]
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
