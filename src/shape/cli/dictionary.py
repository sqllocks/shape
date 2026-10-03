"""``shape dictionary``: a data dictionary from a profile and the project file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

FORMATS = ("md", "html", "json")


def add_arguments(sub: Any) -> None:
    from shape.cli import project as project_cli

    d = sub.add_parser(
        "dictionary",
        help="write a data dictionary (Markdown, HTML or JSON) from a profile",
        description="One entry per table and column: type, null rate, distinct count estimate, "
        "semantic label and confidence, numeric or text length range, format pattern, and from "
        "shape.yml the owner and annotations. Example and top values are written only with "
        "--examples and never for a column classified CONFIDENTIAL or higher. Exit 2 for a "
        "missing or unreadable profile or a source that is not in the project.",
    )
    d.add_argument("profile", metavar="PROFILE.shape")
    d.add_argument("--format", choices=FORMATS, default="md", help="default: md")
    d.add_argument("-o", "--output", required=True, metavar="OUT", help="the file to write")
    d.add_argument(
        "--examples",
        action="store_true",
        help="include example values and top values (columns below CONFIDENTIAL only)",
    )
    project_cli.add_project_flags(d)


def run(a: argparse.Namespace) -> int:
    import shape
    from shape import dictionary
    from shape.cli import errors
    from shape.cli import project as project_cli

    errors.refuse_same_file(a.output, a.profile)
    profile = shape.load(a.profile)
    ctx = project_cli.context(a, profile.name)
    source = ctx.source if ctx is not None else None
    doc = dictionary.build(profile, source=source, examples=a.examples)
    text = {
        "json": dictionary.dumps,
        "md": dictionary.render_markdown,
        "html": dictionary.render_html,
    }[a.format](doc)
    out = Path(a.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(text.encode("utf-8"))
    print(
        json.dumps(
            {
                "output": str(out),
                "format": a.format,
                "dictionary_version": dictionary.VERSION,
                "tables": len(doc["tables"]),
                "columns": sum(len(t["columns"]) for t in doc["tables"]),
            },
            sort_keys=True,
        )
    )
    return 0
