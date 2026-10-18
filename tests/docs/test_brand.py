"""Verify branding, contrast and preserved documentation controls in a real browser."""

import importlib.util
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_branded_site(tmp_path: Path) -> None:
    """Build strictly and verify all four browser presentations without external requests."""
    site = tmp_path / "site"
    subprocess.run(
        [sys.executable, "-m", "mkdocs", "build", "--strict", "--site-dir", str(site)],
        cwd=ROOT,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    spec = importlib.util.spec_from_file_location(
        "brand_preview", ROOT / "scripts/docs_brand_preview.py"
    )
    preview = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(preview)
    results = preview.verify_site(site, tmp_path / "screenshots")
    assert len(results) == 4
    config = (ROOT / "mkdocs.yml").read_text()
    assert "font: false" in config
    assert "https://docs.shapedata.ai/" in config
    assert (ROOT / "docs/CNAME").read_text().strip() == "docs.shapedata.ai"
    for font in ("host-grotesk", "fragment-mono"):
        assert (ROOT / f"docs/assets/fonts/{font}.woff2").read_bytes().startswith(b"wOF2")
        notice = (ROOT / f"docs/assets/fonts/{font}-OFL.txt").read_text()
        assert "SIL OPEN FONT LICENSE Version 1.1" in notice
