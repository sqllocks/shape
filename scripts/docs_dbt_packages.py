"""Prepare pinned local dbt dependencies for account-free documentation checks."""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

PACKAGES = (
    ("dbt_utils", "dbt-labs/dbt-utils", "1.4.0"),
    ("dbt_expectations", "metaplane/dbt-expectations", "0.10.9"),
    ("dbt_date", "godatadriven/dbt-date", "0.10.1"),
)


def prepare(directory: Path) -> None:
    """Fetch public package sources; Shape itself always comes from this checkout."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, repository, tag in PACKAGES:
        destination = directory / name
        if not destination.exists():
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--quiet",
                    "--depth",
                    "1",
                    "--branch",
                    tag,
                    "https://github.com/" + repository + ".git",
                    str(destination),
                ],
                check=True,
                capture_output=True,
            )
        subprocess.run(
            ["git", "-C", str(destination), "diff", "--exit-code"],
            check=True,
            stdout=subprocess.PIPE,
        )
        revision = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", "HEAD"], text=True
        ).strip()
        tag_revision = subprocess.check_output(
            ["git", "-C", str(destination), "rev-parse", tag + "^{commit}"], text=True
        ).strip()
        if revision != tag_revision:
            raise ValueError(f"{name} is not at {tag}")
        print(f"{name}: {tag} ({revision})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    prepare(parser.parse_args().directory)
