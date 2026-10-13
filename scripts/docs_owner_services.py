"""Start Schema Registry only after its Basic auth files exist, then verify readiness."""

from __future__ import annotations

import base64
import http.client
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def write_auth(directory: Path, password: str) -> None:
    """Prepare readable mounted files beneath a private runner-temp parent directory."""
    directory.mkdir(mode=0o755, exist_ok=True)
    directory.chmod(0o755)
    files = {
        "docs-users.properties": f"svc: {password},docs\n",
        "docs-jaas.conf": (
            "ShapeDocs { org.eclipse.jetty.jaas.spi.PropertyFileLoginModule "
            'required file="/run/shape-registry/docs-users.properties"; };\n'
        ),
    }
    for name, content in files.items():
        path = directory / name
        path.write_text(content)
        # The container's appuser can read the mounted directory. Its unmounted
        # parent is mode 0700, so other host users cannot reach these files.
        path.chmod(0o644)


def start_registry(directory: Path, network: str, run_id: str) -> str:
    """Start one registry on Kafka's service network and return Docker's actual ID."""
    settings = {
        "SCHEMA_REGISTRY_HOST_NAME": "schema-registry",
        "SCHEMA_REGISTRY_LISTENERS": "http://0.0.0.0:8081",
        "SCHEMA_REGISTRY_KAFKASTORE_BOOTSTRAP_SERVERS": "PLAINTEXT://kafka:29092",
        "SCHEMA_REGISTRY_AUTHENTICATION_METHOD": "BASIC",
        "SCHEMA_REGISTRY_AUTHENTICATION_REALM": "ShapeDocs",
        "SCHEMA_REGISTRY_AUTHENTICATION_ROLES": "docs",
        "SCHEMA_REGISTRY_OPTS": (
            "-Djava.security.auth.login.config=/run/shape-registry/docs-jaas.conf"
        ),
    }
    command = [
        "docker",
        "run",
        "-d",
        "--name",
        f"docs-schema-registry-{run_id}",
        "--network",
        network,
        "--network-alias",
        "schema-registry",
        "--publish",
        "127.0.0.1:8081:8081",
        "--mount",
        f"type=bind,src={directory},dst=/run/shape-registry,readonly",
    ]
    for name, value in settings.items():
        command.extend(["--env", f"{name}={value}"])
    command.append("confluentinc/cp-schema-registry:7.7.1")
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    container = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{64}", container):
        raise RuntimeError("docker run did not return a full Schema Registry container ID")
    print(f"Started Schema Registry container {container} on Kafka network {network}", flush=True)
    return container


def wait_ready(container: str, password: str, *, timeout: float = 120) -> None:
    """Require authenticated 200 and anonymous 401, tolerating transient startup errors."""
    basic = base64.b64encode(f"svc:{password}".encode()).decode()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    "http://localhost:8081/subjects", headers={"Authorization": "Basic " + basic}
                ),
                timeout=5,
            ) as response:
                if response.status != 200:
                    raise RuntimeError("Schema Registry authenticated request did not return 200")
            try:
                with urllib.request.urlopen("http://localhost:8081/subjects", timeout=5):
                    raise RuntimeError("Schema Registry accepted an unauthenticated request")
            except urllib.error.HTTPError as exc:
                if exc.code != 401:
                    raise
            print("Schema Registry: authenticated request 200, unauthenticated request 401")
            return
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            http.client.RemoteDisconnected,
        ):
            time.sleep(min(2, max(0, deadline - time.monotonic())))
    logs = subprocess.run(
        ["docker", "logs", "--tail", "200", container],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    safe_logs = logs.stdout.replace(password, "<redacted>").replace(basic, "<redacted>")
    print(f"Schema Registry {container}: last 200 log lines:\n{safe_logs}", file=sys.stderr)
    raise TimeoutError(f"Schema Registry was not authenticated and ready within {timeout:g}s")


def main() -> None:
    """Keep auth files outside the checkout and mounted until the job finishes."""
    password = secrets.token_urlsafe(24)
    print(f"::add-mask::{password}", flush=True)
    private = Path(tempfile.mkdtemp(prefix="shape-registry-", dir=os.environ["RUNNER_TEMP"]))
    auth = private / "auth"
    write_auth(auth, password)
    container = start_registry(
        auth, os.environ["SHAPE_DOCS_DOCKER_NETWORK"], os.environ["GITHUB_RUN_ID"]
    )
    with open(os.environ["GITHUB_ENV"], "a") as env:
        env.write(f"REGISTRY_PASSWORD={password}\nSCHEMA_REGISTRY_CONTAINER={container}\n")
    # Do not remove the files on return: Jetty can reopen the property file for later logins.
    wait_ready(container, password)


if __name__ == "__main__":
    main()
