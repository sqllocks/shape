"""Capture the branded docs and check contrast, navigation and local-only requests."""

from __future__ import annotations

import argparse
import functools
import json
import shutil
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(SimpleHTTPRequestHandler):
    """Serve the local built site without request noise."""

    def log_message(self, *args: object) -> None:
        """Keep the preview log limited to verification results."""


CONTRAST = r"""() => {
  const rgb = value => (value.match(/[\d.]+/g) || []).map(Number);
  const composite = (front, back) => front.slice(0,3).map((v,i) =>
    v * (front.length > 3 ? front[3] : 1) + back[i] * (1 - (front.length > 3 ? front[3] : 1)));
  const background = node => {
    if (!node) return [255,255,255];
    return composite(rgb(getComputedStyle(node).backgroundColor), background(node.parentElement));
  };
  const luminance = color => color.map(v => v / 255).map(v =>
    v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4
  ).reduce((sum,v,i) => sum + v * [.2126,.7152,.0722][i],0);
  const findings = [];
  for (const node of document.querySelectorAll('body *')) {
    const rect = node.getBoundingClientRect();
    const style = getComputedStyle(node);
    if (!rect.width || !rect.height || style.visibility !== 'visible'
        || style.opacity === '0') continue;
    if (!Array.from(node.childNodes).some(n =>
        n.nodeType === Node.TEXT_NODE && n.textContent.trim())) continue;
    if (node.closest('svg,script,style,[aria-hidden="true"]')) continue;
    const bg = background(node);
    const fg = composite(rgb(style.color),bg);
    const a = luminance(fg), b = luminance(bg);
    const ratio = (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
    const large = parseFloat(style.fontSize) >= 24
      || (parseFloat(style.fontSize) >= 18.66 && parseInt(style.fontWeight) >= 700);
    const target = large ? 3 : 4.5;
    findings.push({text:node.textContent.trim().slice(0,70), ratio, target,
      color:style.color, background:bg, passes:ratio + .001 >= target});
  }
  return findings;
}"""


def verify_site(site: Path, output: Path) -> list[dict]:
    """Check the real built home in four modes and save screenshots and evidence."""
    output.mkdir(parents=True, exist_ok=True)
    results = []
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        (directory / "dev").symlink_to(site.resolve(), target_is_directory=True)
        # A local mike index models the existing dev deployment, without publishing.
        (directory / "versions.json").write_text(
            json.dumps([{"version": "dev", "title": "dev", "aliases": []}])
        )
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(directory))
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as playwright:
                executable = shutil.which("chromium") or shutil.which("google-chrome")
                browser = playwright.chromium.launch(executable_path=executable)
                for width, device in ((1440, "desktop"), (390, "mobile")):
                    for scheme in ("light", "dark"):
                        page = browser.new_page(
                            viewport={"width": width, "height": 1000 if width > 600 else 844},
                            color_scheme=scheme,
                        )
                        external = []
                        page.on(
                            "request",
                            lambda request, requests=external: (
                                requests.append(request.url)
                                if not request.url.startswith(origin + "/")
                                else None
                            ),
                        )
                        page.goto(origin + "/dev/", wait_until="networkidle")
                        page.evaluate("document.fonts.ready")
                        assert page.evaluate("document.fonts.check('16px \"Host Grotesk\"')")
                        assert page.evaluate("document.fonts.check('16px \"Fragment Mono\"')")
                        assert page.locator(".shape-website-link").is_visible()
                        assert page.locator(".md-version").is_visible(), "Version selector missing"
                        selector = ".shape-logo-dark" if scheme == "dark" else ".shape-logo-light"
                        assert page.locator(".md-header " + selector).is_visible()
                        other = ".shape-logo-light" if scheme == "dark" else ".shape-logo-dark"
                        assert not page.locator(".md-header " + other).is_visible()
                        assert page.evaluate(
                            "document.documentElement.scrollWidth <= innerWidth"
                        ), "Horizontal overflow"
                        findings = page.evaluate(CONTRAST)
                        failures = [item for item in findings if not item["passes"]]
                        assert not failures, json.dumps(failures, indent=2)
                        page.screenshot(
                            path=str(output / f"home-{device}-{scheme}.png"), full_page=True
                        )
                        if width < 600:
                            page.locator('.md-header label[for="__drawer"]').click()
                            assert page.locator(".md-sidebar--primary").is_visible()
                            page.locator(".md-overlay").click(position={"x": width - 20, "y": 200})
                        else:
                            assert page.locator(".md-sidebar--primary").is_visible()
                        toggle = page.locator('label[for="__search"]').first
                        if toggle.is_visible():
                            toggle.click()
                        else:
                            page.locator(".md-search__input").click()
                        search = page.locator(".md-search__input")
                        search.click()
                        search.press_sequentially("profile", delay=50)
                        try:
                            page.wait_for_selector(".md-search-result__item", timeout=10000)
                        except Exception:
                            page.screenshot(path=str(output / f"search-{device}-{scheme}.png"))
                            raise
                        search_findings = page.evaluate(CONTRAST)
                        assert all(item["passes"] for item in search_findings), json.dumps(
                            [item for item in search_findings if not item["passes"]], indent=2
                        )
                        page.goto(
                            origin + "/dev/tutorials/01-first-profile/", wait_until="networkidle"
                        )
                        page.evaluate(
                            "document.querySelectorAll('details').forEach(d => d.open = true)"
                        )
                        code_findings = page.evaluate(CONTRAST)
                        assert all(item["passes"] for item in code_findings), json.dumps(
                            [item for item in code_findings if not item["passes"]], indent=2
                        )
                        assert not external, external
                        results.append(
                            {
                                "device": device,
                                "scheme": scheme,
                                "text_elements_checked": len(findings),
                                "search_elements_checked": len(search_findings),
                                "tutorial_elements_checked": len(code_findings),
                                "minimum_ratio": min(item["ratio"] for item in findings),
                                "external_requests": external,
                            }
                        )
                        page.close()
                browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
    (output / "verification.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


def main() -> None:
    """Run the preview against an already built site."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--site", type=Path, default=ROOT / "site")
    parser.add_argument("--output", type=Path, default=ROOT.parent / "shape-brand-preview")
    args = parser.parse_args()
    print(json.dumps(verify_site(args.site, args.output), indent=2))


if __name__ == "__main__":
    main()
