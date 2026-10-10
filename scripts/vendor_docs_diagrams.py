"""Copy the pinned Mermaid browser bundle and dependency notices from an npm install."""

import hashlib
import json
import shutil
import sys
from pathlib import Path

VERSION = "11.17.2"
ROOT = Path(__file__).resolve().parents[1]


def vendor(node_modules: Path) -> None:
    """Validate the package version, preserve licence files and write asset hashes."""
    package = node_modules / "mermaid"
    metadata = json.loads((package / "package.json").read_text())
    if metadata["version"] != VERSION:
        raise ValueError(f"Expected Mermaid {VERSION}")
    dest = ROOT / "docs/assets/javascripts"
    dest.mkdir(parents=True, exist_ok=True)
    bundle = dest / "mermaid.min.js"
    shutil.copyfile(package / "dist/mermaid.min.js", bundle)
    notices = []
    for path in sorted(node_modules.rglob("package.json")):
        try:
            item = json.loads(path.read_text())
        except (ValueError, UnicodeError):
            continue
        licences = sorted(
            p
            for p in path.parent.iterdir()
            if p.is_file()
            and p.name.lower().startswith(("license", "licence", "copying", "notice"))
        )
        for licence in licences:
            notices += [
                f"{item.get('name')} {item.get('version')} — {licence.name}",
                licence.read_text(errors="replace"),
                "",
            ]
    (dest / "MERMAID_NOTICES.txt").write_text("\n".join(notices))
    (dest / "mermaid-provenance.json").write_text(
        json.dumps(
            {
                "package": "mermaid",
                "version": VERSION,
                "source": f"https://registry.npmjs.org/mermaid/-/mermaid-{VERSION}.tgz",
                "sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Vendored Mermaid {VERSION} with dependency licence notices")


if __name__ == "__main__":
    vendor(Path(sys.argv[1]))
