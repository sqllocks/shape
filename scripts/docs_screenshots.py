"""Regenerate light and dark HTML report screenshots from the committed profile."""

import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

import shape

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Render the real report in a browser and save both color schemes."""
    profile = shape.load(ROOT / "docs/assets/orders.shape")
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.html"
        report.write_text(profile.to_html())
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=shutil.which("chromium"))
            for scheme in ("light", "dark"):
                page = browser.new_page(
                    viewport={"width": 1200, "height": 900}, color_scheme=scheme
                )
                page.set_content(report.read_text())
                page.screenshot(path=str(ROOT / f"docs/assets/report-{scheme}.png"), full_page=True)
                page.close()
            browser.close()
    print("Wrote report-light.png and report-dark.png")


if __name__ == "__main__":
    main()
