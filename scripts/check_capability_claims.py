"""Reject public documentation claims beyond the registered release capabilities."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gen_capabilities_page import ROOT, Capability, capabilities, planned_capabilities  # noqa: E402

# Keep claim grammar here. Bounded phrases stop at sentence/clause boundaries and exclude
# reading an export or a profile artifact, which does not claim a live database source.
WORDS = r"(?:[\w'-]+\s+){0,8}?"
PROPER_NAME = r"(?-i:[A-Z][A-Za-z0-9-]*(?:\s+[A-Z][A-Za-z0-9-]*){0,3})"
PATTERNS = (
    ("reads", r"\bprofil(?:e|es|ing)\s+" + WORDS + r"from\s+(?P<system>{systems})\b(?!://)"),
    ("reads", r"\bprofil(?:e|es|ing)\s+(?P<system>{systems})\s+(?:tables?|files?|workbooks?)\b"),
    ("reads", r"\bread(?:s|ing)?\s+" + WORDS + r"(?P<system>{systems})\b(?!://)"),
    (
        "reads",
        r"\bread(?:s|ing)?\s+"
        + WORDS
        + r"(?:data|tables?|rows|batches)\s+from\s+(?P<system>{systems})\b(?!://)",
    ),
    ("reads", r"\bconnect(?:s|ing)?\s+to\s+(?P<system>{systems})\b(?!://)"),
    (
        "writes",
        r"\b(?:write|writes|writing|generate|generates|generating|seed|seeds|seeding)\s+"
        + WORDS
        + r"(?:into|to)\s+(?P<system>{systems})\b(?!://)",
    ),
    ("reads", r"\bshape\s+(?P<command>profile|stream-profile)\s+(?P<scheme>[a-z][\w+.-]*)://"),
    ("writes", r"--to\s+(?P<scheme>[a-z][\w+.-]*)://"),
    # A direct Shape claim about an unknown proper name must not escape the inventory.
    (
        "reads",
        r"\bShape\s+profil(?:e|es|ing)\s+(?P<unknown>"
        + PROPER_NAME
        + r")\s+(?:tables?|files?|workbooks?)\b",
    ),
    ("reads", r"\bShape\s+reads?\s+(?:from\s+)?(?P<unknown>" + PROPER_NAME + r")\b"),
    (
        "writes",
        r"\bShape\s+(?:writes?|generates?|seeds?)\s+"
        + WORDS
        + r"(?:to|into)\s+(?P<unknown>"
        + PROPER_NAME
        + r")\b",
    ),
)
NEGATION = re.compile(r"\b(?:not|never|cannot|no|unsupported)\b|\b\w+n['’]t\b", re.I)
PLANNED = re.compile(r"\b(?:planned|coming|next release)\b", re.I)
EXPORT = re.compile(r"\b(?:export(?:s|ed)?|files?|artifacts?)\b", re.I)
METADATA_SUFFIX = re.compile(r"\s+(?:document\s+(?:described|defined)\s+by\b|event['’]s\b)", re.I)
SCHEMA_FORMAT = re.compile(r"^JSON\s+Schema\b", re.I)
METADATA_CONTEXT = re.compile(r"\b(?:shape-[\w-]+-config|reference dataset name)\b", re.I)


def source_files(root: Path = ROOT) -> list[Path]:
    """Public Markdown: language-check scope plus the released plugin READMEs."""
    paths = [
        p
        for p in (root / "docs").rglob("*.md")
        if not {"plans", "talks"}.intersection(p.relative_to(root / "docs").parts)
    ]
    paths += [
        root / n
        for n in (
            "README.md",
            "CONTRIBUTING.md",
            "GOVERNANCE.md",
            "SECURITY.md",
            "CHANGELOG.md",
            "THIRD_PARTY_NOTICES.md",
        )
    ]
    paths += list((root / "plugins").glob("*/README.md"))
    return sorted(p for p in paths if p.is_file())


def claims(text: str, items: list[Capability], planned: list[dict]) -> list[tuple[int, str, str]]:
    """Unsupported (line, direction, system); planned and explicitly negated prose is allowed."""
    aliases: dict[str, str] = {}
    supported: dict[str, set[str]] = {}
    routes: dict[str, set[str]] = {}
    for c in items:
        key = c.system.casefold()
        supported.setdefault(key, set())
        if c.exposed:
            supported[key].add(c.direction)
        for alias in (c.system, c.name, *(e.lstrip(".") for e in c.extensions)):
            aliases.setdefault(alias.casefold(), key)
        for scheme in c.schemes:
            # Shared file:// handlers must not grant one format another format's reads.
            aliases.setdefault(scheme.casefold(), scheme.casefold())
            if c.exposed:
                routes.setdefault(scheme.casefold(), set()).add(c.command)
                supported.setdefault(aliases[scheme.casefold()], set()).add(c.direction)
    # The runtime also names unsupported schemes in its capability errors. Recognise
    # those names in prose without granting them any direction.
    from shape.plugins.schemes import _KNOWN

    for scheme, system in _KNOWN.items():
        key = aliases.get(scheme, aliases.get(system.casefold(), system.casefold()))
        aliases.setdefault(scheme, key)
        if scheme in routes:
            aliases[system.casefold()] = key
        else:
            aliases.setdefault(system.casefold(), key)
    plans: dict[tuple[str, str], set[str]] = {}
    for row in planned:
        key = row["system"].casefold()
        aliases[key] = key
        plans.setdefault((key, row["direction"]), set()).add(row["w9"].casefold())
    systems = "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True) if a != "-")
    system_list = r"(?P<system>(?:{systems})(?:\s*(?:,|and\b|or\b)\s*(?:{systems}))*)"
    patterns = [
        (
            direction,
            re.compile(
                pattern.replace(r"(?P<system>{systems})", system_list).replace(
                    "{systems}", systems
                ),
                re.I,
            ),
        )
        for direction, pattern in PATTERNS
    ]
    # Paragraphs can wrap a claim across Markdown lines. Sentence boundaries remain intact;
    # retain offsets so a failure points to the claim's original line.
    cleaned = re.sub(r"<!--.*?-->", lambda m: "\n" * m[0].count("\n"), text, flags=re.S)
    cleaned = re.sub(
        r"\[([^\]]+)\]\([^\)]+\)", lambda m: m[1] + " " * (len(m[0]) - len(m[1])), cleaned
    )
    cleaned = cleaned.replace("`", " ").replace("*", " ")
    failures = set()
    for sentence in re.finditer(r"[^.!?;\n]+(?:\n(?!\s*\n)[^.!?;\n]+)*", cleaned):
        content = sentence[0]
        paragraph_start = cleaned.rfind("\n\n", 0, sentence.start()) + 2
        paragraph_end = cleaned.find("\n\n", sentence.end())
        paragraph = cleaned[
            paragraph_start : paragraph_end if paragraph_end != -1 else len(cleaned)
        ]
        if cleaned[sentence.end() : sentence.end() + 1] == "?" and re.match(
            r"^[#\s]*(?:does|do|can|will|is)\b", content, re.I
        ):
            continue
        for direction, pattern in patterns:
            for match in pattern.finditer(content):
                prefix = content[: match.start()].rsplit(",", 1)[-1]
                if NEGATION.search(" ".join(prefix.split()[-5:])):
                    continue
                system = match.groupdict().get("system") or match.groupdict().get("unknown")
                if system and EXPORT.search(match[0].split(system)[0]):
                    continue
                objects = (
                    [(match["scheme"], match.end())]
                    if match.groupdict().get("scheme")
                    else [(match["unknown"], match.end("unknown"))]
                    if match.groupdict().get("unknown")
                    else [
                        (m[0], match.start("system") + m.end())
                        for m in re.finditer(systems, match["system"], re.I)
                    ]
                )
                for raw, target_end in objects:
                    if (
                        not match.groupdict().get("scheme")
                        and not re.match(r"(?:Shape\s+)?profil", match[0], re.I)
                        and (
                            METADATA_SUFFIX.match(content[target_end:])
                            or SCHEMA_FORMAT.match(raw + content[target_end:])
                            or METADATA_CONTEXT.search(content)
                            or re.match(r"reads?\s+as\s+", match[0], re.I)
                        )
                    ):
                        continue
                    if (
                        not match.groupdict().get("scheme")
                        and not re.match(r"(?:Shape\s+)?profil", match[0], re.I)
                        and re.match(
                            r"(?:['’]s\s+|(?:['’]s)?\s+(?:project|job|layout|schema)(?:['’]s)?\s+)"
                            r"(?:files?|artifacts?|[\w-]+\.(?:json|ya?ml|csv|parquet))\b",
                            cleaned[sentence.start() + target_end :],
                            re.I,
                        )
                    ):
                        continue
                    if re.match(
                        r"\s+(?:is|are)\s+(?:not|never)\s+(?:supported|available|possible)\b",
                        content[target_end:],
                        re.I,
                    ):
                        continue
                    if re.match(r"^\s*how\s+", content, re.I) and re.search(
                        r"\bnot verified\b", paragraph, re.I
                    ):
                        continue  # An indirect question in an owner checklist is not a claim.
                    if raw.casefold() == "sql" and re.match(
                        r"\s+(?:DDL|CREATE\s+TABLE)\b", content[target_end:], re.I
                    ):
                        continue  # Reading SQL syntax from a file is not a live database source.
                    key = aliases.get(raw.casefold(), raw.casefold().split("+", 1)[0])
                    if match.groupdict().get("scheme"):
                        commands = routes.get(raw.casefold(), set())
                        command = match.groupdict().get("command")
                        if (
                            command in commands
                            if command
                            else commands.intersection({"generate --to", "emit"})
                        ):
                            continue
                    elif direction in supported.get(key, set()):
                        continue
                    if PLANNED.search(content) and any(
                        re.search(r"\b" + re.escape(w9) + r"\b", content, re.I)
                        for w9 in plans.get((key, direction), ())
                    ):
                        continue
                    line = cleaned.count("\n", 0, sentence.start() + match.start()) + 1
                    failures.add((line, direction, raw))
    return sorted(failures)


def check(paths: list[Path], items: list[Capability] | None = None) -> list[str]:
    items = capabilities() if items is None else items
    planned = planned_capabilities()
    failures = []
    for path in paths:
        for line, direction, system in claims(path.read_text(), items, planned):
            name = path.relative_to(ROOT) if path.is_relative_to(ROOT) else path
            failures.append(f"{name}:{line}: unsupported {direction} claim for {system}")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    failures = check(args.paths or source_files())
    for failure in failures:
        print(failure)
    print(f"Capability claims: {len(failures)} unsupported claim(s)")
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
