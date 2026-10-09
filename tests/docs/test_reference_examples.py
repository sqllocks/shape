"""Execute restored reference commands against the editable repository and plugins."""

from __future__ import annotations

import difflib
import json
import os
import re
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "tests/docs/reference_examples.json"
BLOCK = re.compile(
    r"<!-- example: (\d+) -->\n\n```bash {\.runnable-reference}\n(.*?)\n```\n\n"
    r'\?\?\? info "Output \(exit (-?\d+)\)"\n\n    ```text {\.expected}\n(.*?)    ```',
    re.S,
)
PAGES = (
    sorted(
        {
            item["page"]
            for item in json.loads(MANIFEST.read_text())
            if item["kind"] in {"local", "bootstrap"}
        }
    )
    if MANIFEST.exists()
    else []
)


class Receiver(BaseHTTPRequestHandler):
    """Accept documentation notifications locally without forwarding them."""

    def do_POST(self) -> None:
        """Consume one event and acknowledge it."""
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        """Keep the fixture's service log out of command transcripts."""


@pytest.fixture(scope="session")
def webhook() -> str:
    """Provide a receiver bound only to loopback."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize("page", PAGES, ids=lambda p: Path(p).stem)
def test_reference_page(page: str, tmp_path: Path, webhook: str) -> None:
    """Run setup and every page command, checking the entire recorded stream."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from docs_transcripts import comparable

    env = {
        **os.environ,
        "PATH": str(ROOT / ".venv/bin") + ":" + os.environ["PATH"],
        "PYTHONPATH": str(ROOT / "src") + ":.",
        "SHAPE_KERNEL": "python",
        "SHAPE_DOCS_REPO": str(ROOT),
        "SHAPE_HOME": str(tmp_path / ".shape-state"),
        "SHAPE_JOBS_DIR": str(tmp_path / "jobs"),
        "SHAPE_PROFILE_REGISTRY": str(tmp_path / "profile-registry"),
        "SHAPE_HEALTHCARE_CODES_DIR": str(tmp_path / "healthcare-assets"),
        "SHAPE_KEY_PASSPHRASE": "local-example",
        "SHAPE_SIGNING_PASSPHRASE": "local-example",
        "SHAPE_SIGNING_KEY": "file://release.key",
        "SHAPE_DOCS_WEBHOOK": webhook,
        "SHAPE_DOCS_DBT_PACKAGES": os.environ.get(
            "SHAPE_DOCS_DBT_PACKAGES", "/tmp/docs-dbt-packages"
        ),
        "SHAPE_DBT_PACKAGES_FILE": str(tmp_path / "local-packages.yml"),
        "DOCS_CA_BUNDLE": os.environ.get("DOCS_CA_BUNDLE", "/etc/ssl/certs/ca-certificates.crt"),
        "DOCKER_CONFIG": str(tmp_path / "docker-config"),
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "DBT_USE_COLORS": "false",
        "DOCS_PROXY_IP": os.environ.get("DOCS_PROXY_IP", "127.0.0.1"),
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_CACHE_DIR": "1",
        "NO_COLOR": "1",
        "COLUMNS": "100",
    }
    blocks = BLOCK.findall((ROOT / page).read_text())
    assert blocks, page
    for number, command, code, expected_block in blocks:
        expected = "\n".join(line.removeprefix("    ") for line in expected_block.splitlines())
        if expected == "(no output)":
            expected = ""
        run = subprocess.run(
            ["bash", "--noprofile", "--norc", "-c", command],
            cwd=tmp_path,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=1200,
        )
        transcript = tmp_path / f".doc-output-{number}.txt"
        transcript.write_text(run.stdout)
        assert run.returncode == int(code), (
            f"{page}:{number} exited {run.returncode}, expected {code}; transcript: {transcript}\n"
            + run.stdout[-1000:]
        )
        crypto = page.endswith(("SIGNING.md", "PRIVACY_MODEL.md"))
        actual = comparable(run.stdout.rstrip("\n"), tmp_path, ROOT, crypto=crypto)
        recorded = comparable(expected, tmp_path, ROOT, crypto=crypto)
        if actual != recorded:
            difference = list(difflib.unified_diff(recorded.splitlines(), actual.splitlines()))
            pytest.fail(f"{page}:{number}; transcript: {transcript}\n" + "\n".join(difference[:80]))
        if number == "999" and (tmp_path / ".venv/bin/python").exists():
            env["PATH"] = str(tmp_path / ".venv/bin") + ":" + env["PATH"]


def test_restored_example_inventory() -> None:
    """Every inventoried example remains on its page with the appropriate evidence."""
    assert MANIFEST.exists()
    items = json.loads(MANIFEST.read_text())
    assert len(items) == 206
    for item in items:
        text = (ROOT / item["page"]).read_text()
        assert f"<!-- example: {item['index']} -->" in text, item
        if item["kind"].startswith("cloud:"):
            assert f"Needs a {item['kind'].split(':', 1)[1]} account. Not run in CI." in text
        elif item["kind"] in {"local", "bootstrap"}:
            assert str(item["index"]) in {b[0] for b in BLOCK.findall(text)}, item
