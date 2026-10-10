"""Check DCO sign-off trailers in every commit of a pull request."""

import re
import subprocess
import sys


def check(base: str, head: str) -> list[str]:
    """Return commits with no Signed-off-by name and email trailer."""
    commits = subprocess.check_output(
        ["git", "rev-list", f"{base}..{head}"], text=True
    ).splitlines()
    missing = []
    for commit in commits:
        body = subprocess.check_output(["git", "show", "-s", "--format=%B", commit], text=True)
        trailers = subprocess.check_output(
            ["git", "interpret-trailers", "--parse"], input=body, text=True
        )
        if not re.search(r"^Signed-off-by: .+ <[^<>\s]+@[^<>\s]+>$", trailers, re.M | re.I):
            missing.append(commit)
    return missing


if __name__ == "__main__":
    missing = check(*sys.argv[1:])
    for commit in missing:
        print(f"Missing DCO sign-off: {commit}")
    print(f"DCO: {len(missing)} missing sign-offs")
    sys.exit(bool(missing))
