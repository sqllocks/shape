"""Write or check ``THIRD_PARTY_NOTICES_RUST.md``: the licences of the Rust crates in the kernel.

The platform wheels link ``shape._kernel`` statically against the crates in
``rust/shape-kernel/Cargo.lock`` (Apache-2.0, BSD-2-Clause, BSL-1.0, MIT, Unicode-3.0 and others),
so each wheel carries their licence texts and notices in ``dist-info/licenses``
(``license-files`` in ``pyproject.toml``).

    python scripts/rust_notices.py write   # regenerate after Cargo.lock changes (needs cargo)
    python scripts/rust_notices.py check   # every locked crate is listed (no cargo needed)

The file covers every package in ``Cargo.lock``, a superset of what is linked (it includes the
build-time crates), so the check needs only the lock file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CRATE = ROOT / "rust" / "shape-kernel"
LOCK = CRATE / "Cargo.lock"
OUT = ROOT / "THIRD_PARTY_NOTICES_RUST.md"
ROOT_CRATE = "shape-kernel"
LICENCE_FILE = re.compile(r"^(licen[cs]e|copying|notice|unlicense)([-._].*)?$", re.IGNORECASE)
MIT_TEXT = """\
Permission is hereby granted, free of charge, to any person obtaining a copy of this software
and associated documentation files (the "Software"), to deal in the Software without
restriction, including without limitation the rights to use, copy, modify, merge, publish,
distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all copies or
substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
"""


def locked_crates(lock: Path = LOCK) -> list[tuple[str, str]]:
    """(name, version) of every third-party package in the lock file."""
    data = tomllib.loads(lock.read_text(encoding="utf-8"))
    return sorted(
        (p["name"], p["version"]) for p in data.get("package", []) if p["name"] != ROOT_CRATE
    )


def listed_crates(text: str) -> set[tuple[str, str]]:
    """(name, version) of every row of the crate table in a notices file."""
    return set(re.findall(r"^\| `([^`]+)` \| ([^ |]+) \|", text, flags=re.MULTILINE))


def check(out: Path = OUT, lock: Path = LOCK) -> list[str]:
    if not out.is_file():
        return [f"{out.name} is missing: run python scripts/rust_notices.py write"]
    listed = listed_crates(out.read_text(encoding="utf-8"))
    return [
        f"{name} {version} is in Cargo.lock but not in {out.name}: "
        "run python scripts/rust_notices.py write"
        for name, version in locked_crates(lock)
        if (name, version) not in listed
    ]


def _metadata() -> list[dict[str, Any]]:
    run = subprocess.run(
        [
            "cargo",
            "metadata",
            "--format-version",
            "1",
            "--locked",
            "--manifest-path",
            str(CRATE / "Cargo.toml"),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return list(json.loads(run.stdout)["packages"])


def _licence_texts(pkg: dict[str, Any]) -> list[tuple[str, str]]:
    folder = Path(pkg["manifest_path"]).parent
    texts = [
        (p.name, p.read_text(encoding="utf-8", errors="replace").strip())
        for p in sorted(folder.iterdir())
        if p.is_file() and LICENCE_FILE.match(p.name)
    ]
    if pkg.get("license_file"):
        p = folder / pkg["license_file"]
        if p.is_file() and p.name not in {n for n, _ in texts}:
            texts.append((p.name, p.read_text(encoding="utf-8", errors="replace").strip()))
    return texts


def render(packages: list[dict[str, Any]]) -> str:
    wanted = set(locked_crates())
    pkgs = sorted(
        (p for p in packages if (p["name"], p["version"]) in wanted),
        key=lambda p: (p["name"], p["version"]),
    )
    missing = wanted - {(p["name"], p["version"]) for p in pkgs}
    if missing:
        raise SystemExit(f"cargo metadata does not describe {sorted(missing)}")
    lines = [
        "# Third-Party Notices: Rust crates",
        "",
        "The platform wheels of sqllocks-shape contain `shape._kernel`, compiled from",
        "`rust/shape-kernel` and statically linked with the Rust crates below (the list is every",
        "package in `Cargo.lock`, including crates used only at build time). Each crate is used",
        "under the licence named in its manifest; where it offers a choice, under any one of the",
        "licences offered. Their licence texts and notices follow the table.",
        "",
        "Generated by `python scripts/rust_notices.py write`; do not edit by hand.",
        "",
        "| Crate | Version | Licence | Source |",
        "|---|---|---|---|",
    ]
    for p in pkgs:
        source = p.get("repository") or f"https://crates.io/crates/{p['name']}"
        lines.append(f"| `{p['name']}` | {p['version']} | {p.get('license') or '?'} | {source} |")
    groups: dict[str, tuple[str, str, list[str]]] = {}
    no_file: list[dict[str, Any]] = []
    for p in pkgs:
        texts = _licence_texts(p)
        if not texts:
            no_file.append(p)
        for name, text in texts:
            key = hashlib.sha256(text.encode("utf-8")).hexdigest()
            groups.setdefault(key, (name, text, []))[2].append(f"{p['name']} {p['version']}")
    lines += ["", "## Licence texts and notices", ""]
    for name, text, users in sorted(groups.values(), key=lambda g: (g[2][0], g[0])):
        lines += [f"### {name}: {', '.join(users)}", "", "```text", text, "```", ""]
    if no_file:
        lines += [
            "## Crates published without a licence file",
            "",
            "These crates ship no licence text. Each offers the MIT licence in its manifest and is",
            "used under it, copyright its authors as listed:",
            "",
        ]
        for p in no_file:
            authors = ", ".join(p.get("authors") or []) or "the crate's authors"
            lines.append(f"- `{p['name']}` {p['version']} ({p.get('license')}): {authors}")
        lines += ["", "```text", MIT_TEXT.rstrip(), "```", ""]
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=["write", "check"])
    a = ap.parse_args(argv)
    if a.action == "write":
        OUT.write_text(render(_metadata()), encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")
    problems = check()
    for p in problems:
        print(f"FAIL {p}", file=sys.stderr)
    if not problems:
        print(f"{OUT.name}: {len(locked_crates())} crates covered")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
