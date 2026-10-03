"""A folder of ``.shape`` files next to Fabric items survives Fabric Git integration both ways.

The workspace under test must already be connected to a Git repository (and initialised). The
test pushes ``shape/`` (two ``.shape`` files written by ``shape profile``) and a ``.gitattributes``
line from ``shape git-setup`` together with one changed item definition, runs "update from Git",
changes the item in the workspace, runs "commit to Git", fetches the branch and compares bytes.

The workflow job (``fabric-git-sync-live``) runs only when the secrets are set; run by hand with

    FABRIC_TENANT_ID=... FABRIC_CLIENT_ID=... FABRIC_CLIENT_SECRET=... \\
    FABRIC_WORKSPACE_ID=... FABRIC_GIT_REMOTE=https://github.com/<owner>/<repo>.git \\
    FABRIC_GIT_TOKEN=... [FABRIC_GIT_USER=x-access-token] [SHAPE_LIVE_RESULT=result.json] \\
    pytest -m live plugins/shape-fabric/tests/test_live_git_sync.py

``FABRIC_GIT_TOKEN`` needs write access to the repository. A missing variable fails the test with
the variable's name (nothing is silently skipped). When ``SHAPE_LIVE_RESULT`` names a file, a
``shape-live-check`` JSON result is written there, also when the test fails.
"""

import base64
import csv
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest
from shape_fabric.livecheck import LiveCheck
from shape_fabric.notebook import item_definition

from shape import __version__
from shape.scale.http import FABRIC_API, Http, HttpResponse

pytestmark = pytest.mark.live

NEEDED = (
    "FABRIC_TENANT_ID",
    "FABRIC_CLIENT_ID",
    "FABRIC_CLIENT_SECRET",
    "FABRIC_WORKSPACE_ID",
    "FABRIC_GIT_REMOTE",
    "FABRIC_GIT_TOKEN",
)
OPERATION_SECONDS = 900


def need(name):
    value = os.environ.get(name)
    assert value, f"{name} is not set (live tests need it)"
    return value


def notebook(token):
    return {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [f"print('shape git sync {token}')"],
            }
        ],
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
    }


class Git:
    """``git`` against the remote, authenticated by an environment variable (never argv)."""

    def __init__(self, remote, token, user, folder):
        self.folder = folder
        basic = base64.b64encode(f"{user}:{token}".encode()).decode()
        self.env = {
            **os.environ,
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.remote = remote

    def run(self, *args, binary=False, cwd=None):
        done = subprocess.run(  # noqa: S603
            ["git", *args],  # noqa: S607
            cwd=cwd or self.folder,
            env=self.env,
            capture_output=True,
            check=False,
        )
        if done.returncode:
            raise AssertionError(f"git {args[0]} failed: {done.stderr.decode(errors='replace')}")
        return done.stdout if binary else done.stdout.decode()

    def clone(self, branch):
        subprocess.run(  # noqa: S603
            ["git", "clone", "--quiet", "--single-branch", "--branch", branch, self.remote, "."],  # noqa: S607
            cwd=self.folder,
            env=self.env,
            capture_output=True,
            check=True,
        )
        self.run("config", "user.name", "shape-live-check")
        self.run("config", "user.email", "shape-live-check@users.noreply.github.com")

    def head(self):
        return self.run("rev-parse", "HEAD").strip()

    def fetch_reset(self, branch):
        self.run("fetch", "--quiet", "origin", branch)
        self.run("reset", "--quiet", "--hard", f"origin/{branch}")

    def show(self, branch, path):
        return self.run("show", f"origin/{branch}:{path}", binary=True)

    def files_under(self, branch, folder):
        out = self.run("ls-tree", "-r", "--name-only", f"origin/{branch}", "--", f"{folder}/")
        return sorted(line for line in out.splitlines() if line)


class Fabric:
    def __init__(self, http, workspace):
        self.http = http
        self.ws = workspace

    def wait(self, response, what):
        """Follow a long-running operation (HTTP 202) to its end."""
        if response.status != 202:
            return response
        location = response.headers.get("location", "")
        assert location, f"{what}: accepted without an operation URL"
        deadline = time.monotonic() + OPERATION_SECONDS
        while time.monotonic() < deadline:
            wait = response.headers.get("retry-after", "")
            time.sleep(min(float(wait) if wait.isdigit() else 5.0, 30.0))
            response = self.http.request("GET", location)
            state = response.json().get("status", "")
            assert state != "Failed", f"{what}: the operation failed"
            if state == "Succeeded":
                return response
        raise AssertionError(f"{what}: timed out after {OPERATION_SECONDS} s")

    def connection(self):
        doc = self.http.request("GET", f"{FABRIC_API}/workspaces/{self.ws}/git/connection").json()
        details = doc.get("gitProviderDetails") or {}
        assert doc.get("gitConnectionState") == "ConnectedAndInitialized", (
            "the workspace is not connected to Git (and initialised)"
        )
        return details["branchName"], (details.get("directoryName") or "").strip("/")

    def status(self):
        response = self.http.request("GET", f"{FABRIC_API}/workspaces/{self.ws}/git/status")
        if response.status == 202:
            done = self.wait(response, "git status")
            response = self.http.request("GET", done_location(done, response))
        return response.json()

    def create_notebook(self, name, token):
        response = self.http.request(
            "POST",
            f"{FABRIC_API}/workspaces/{self.ws}/items",
            body=item_definition(notebook(token), name),
            timeout=60.0,
        )
        response = self.wait(response, "create notebook")
        if response.status in (200, 201) and response.json().get("id"):
            return response.json()["id"]
        return self.find(name)

    def find(self, name):
        url = f"{FABRIC_API}/workspaces/{self.ws}/items?type=Notebook"
        for item in self.http.request("GET", url).json().get("value", []):
            if item.get("displayName") == name:
                return item["id"]
        raise AssertionError("the notebook was not found in the workspace")

    def update_notebook(self, item_id, name, token):
        body = {"definition": item_definition(notebook(token), name)["definition"]}
        self.wait(
            self.http.request(
                "POST",
                f"{FABRIC_API}/workspaces/{self.ws}/items/{item_id}/updateDefinition",
                body=body,
                timeout=60.0,
            ),
            "update item",
        )

    def update_from_git(self, remote_hash, head):
        body = {
            "remoteCommitHash": remote_hash,
            "workspaceHead": head,
            "options": {"allowOverrideItems": True},
        }
        self.wait(
            self.http.request(
                "POST",
                f"{FABRIC_API}/workspaces/{self.ws}/git/updateFromGit",
                body=body,
                timeout=60.0,
            ),
            "update from Git",
        )

    def commit_to_git(self, head):
        body = {"mode": "All", "workspaceHead": head, "comment": "shape live check"}
        self.wait(
            self.http.request(
                "POST",
                f"{FABRIC_API}/workspaces/{self.ws}/git/commitToGit",
                body=body,
                timeout=60.0,
            ),
            "commit to Git",
        )

    def delete(self, item_id):
        self.http.request("DELETE", f"{FABRIC_API}/workspaces/{self.ws}/items/{item_id}")


def done_location(done: HttpResponse, accepted: HttpResponse) -> str:
    """Where the result of a finished operation is read: the operation URL plus ``/result``."""
    return accepted.headers["location"].rstrip("/") + "/result"


def write_shape_files(folder: Path) -> list[Path]:
    """Two ``.shape`` files, each written by ``shape profile`` from a generated table."""
    folder.mkdir(parents=True, exist_ok=True)
    tables = {
        "customers": (["id", "country"], [(i, ("DE", "FR", "US")[i % 3]) for i in range(60)]),
        "orders": (["id", "amount"], [(i, round(5 + (i * 7) % 90 + 0.25, 2)) for i in range(120)]),
    }
    out = []
    for name, (header, rows) in tables.items():
        source = folder / f"{name}.csv"
        with source.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(rows)
        target = folder / f"{name}.shape"
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "shape", "profile", str(source), "-o", str(target)],
            check=True,
            capture_output=True,
        )
        source.unlink()
        out.append(target)
    return out


def item_folder(repo: Path, directory: str, name: str) -> Path:
    base = repo / directory if directory else repo
    found = sorted(base.glob(f"{name}.Notebook"))
    assert found, f"no folder {name}.Notebook in the repository after the first commit"
    return found[0]


def change_definition(folder: Path, token: str) -> None:
    """One changed item definition, whichever form the workspace stores the notebook in."""
    contents = sorted(folder.glob("notebook-content.*"))
    assert contents, f"no notebook-content file in {folder.name}"
    path = contents[0]
    if path.suffix == ".ipynb":
        doc = json.loads(path.read_text(encoding="utf-8"))
        doc.setdefault("metadata", {})["shape_live_check"] = token
        path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    else:
        text = path.read_text(encoding="utf-8")
        path.write_text(text.rstrip("\n") + f"\n\n# shape live check {token}\n", encoding="utf-8")


def test_shape_files_survive_a_git_sync_in_both_directions(tmp_path):
    for name in NEEDED:  # the first missing one is the one named
        need(name)
    from azure.identity import ClientSecretCredential

    secrets = [need("FABRIC_CLIENT_SECRET"), need("FABRIC_GIT_TOKEN")]
    credential = ClientSecretCredential(
        need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID"), need("FABRIC_CLIENT_SECRET")
    )
    secrets.append(credential.get_token("https://api.fabric.microsoft.com/.default").token)
    fabric = Fabric(Http(secrets[-1]), need("FABRIC_WORKSPACE_ID"))
    workspace_id = need("FABRIC_WORKSPACE_ID")
    check = LiveCheck(
        "fabric-git-sync",
        __version__,
        secrets=[*secrets, workspace_id, need("FABRIC_TENANT_ID"), need("FABRIC_CLIENT_ID")],
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    git = Git(
        need("FABRIC_GIT_REMOTE"),
        need("FABRIC_GIT_TOKEN"),
        os.environ.get("FABRIC_GIT_USER", "x-access-token"),
        repo,
    )
    tag = uuid.uuid4().hex[:8]
    name = f"shape_git_sync_{tag}"
    item_id = None
    branch, directory = "", ""
    try:
        with check.step("connection"):
            branch, directory = fabric.connection()
            git.clone(branch)
        with check.step("setup-item"):
            item_id = fabric.create_notebook(name, "initial")
            status = fabric.status()
            fabric.commit_to_git(status["workspaceHead"])
            git.fetch_reset(branch)
        with check.step("push"):
            shape_dir = (repo / directory / "shape") if directory else repo / "shape"
            files = write_shape_files(shape_dir)
            subprocess.run(  # noqa: S603
                [sys.executable, "-m", "shape", "git-setup", "--repo", str(repo)],
                check=True,
                capture_output=True,
            )
            change_definition(item_folder(repo, directory, name), tag)
            git.run("add", "--all")
            git.run("commit", "--quiet", "-m", "shape live check: shape/ and one item change")
            git.run("push", "--quiet", "origin", f"HEAD:{branch}")
            pushed = git.head()
            relative = [(f.relative_to(repo).as_posix(), f.read_bytes()) for f in files]
            relative.append((".gitattributes", (repo / ".gitattributes").read_bytes()))
        with check.step("update-from-git"):
            fabric.update_from_git(pushed, fabric.status()["workspaceHead"])
            after = fabric.status()
            assert after["workspaceHead"] == pushed, "the workspace did not move to the commit"
            assert not [c for c in after["changes"] if c.get("conflictType") == "Conflict"], (
                "the workspace reports a conflict"
            )
            assert not after["changes"], "the item change did not arrive (changes remain)"
        with check.step("commit-to-git"):
            fabric.update_notebook(item_id, name, f"workspace-{tag}")
            changed = fabric.status()
            assert changed["changes"], "the workspace change was not seen"
            fabric.commit_to_git(changed["workspaceHead"])
        with check.step("verify-files"):
            git.run("fetch", "--quiet", "origin", branch)
            for path, content in relative:
                assert git.show(branch, path) == content, f"{path} is not byte-identical"
            shape_prefix = f"{directory}/shape" if directory else "shape"
            expected = sorted(p for p, _ in relative if p.startswith(shape_prefix + "/"))
            assert git.files_under(branch, shape_prefix) == expected, (
                "a file was added to or removed from shape/"
            )
    finally:
        with check.step("cleanup"):
            errors = []
            try:
                if branch:
                    git.fetch_reset(branch)
                    for target in (
                        (directory + "/shape") if directory else "shape",
                        f"{directory + '/' if directory else ''}{name}.Notebook",
                    ):
                        if (repo / target).exists():
                            git.run("rm", "-r", "-q", target)
                    if git.run("status", "--porcelain").strip():
                        git.run("commit", "--quiet", "-m", "shape live check: clean up")
                        git.run("push", "--quiet", "origin", f"HEAD:{branch}")
            except Exception as exc:  # noqa: BLE001 - clean up everything, report after
                errors.append(f"git: {exc}")
            try:
                if item_id:
                    fabric.delete(item_id)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"item: {exc}")
            if errors:
                raise AssertionError("; ".join(errors))
        target = os.environ.get("SHAPE_LIVE_RESULT")
        if target:
            check.write(target)
