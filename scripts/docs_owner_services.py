"""Prepare authentication and verify the real Schema Registry before capturing docs."""

from __future__ import annotations

import io
import os
import secrets
import subprocess
import tarfile
import time
import urllib.error
import urllib.request


def main() -> None:
    """Configure Basic auth without putting the disposable password in a repo or log."""
    password = secrets.token_urlsafe(24)
    print(f"::add-mask::{password}", flush=True)
    with open(os.environ["GITHUB_ENV"], "a") as env:
        env.write("REGISTRY_PASSWORD=" + password + "\n")
    container = os.environ["SCHEMA_REGISTRY_CONTAINER"]
    files = {
        "/tmp/docs-users.properties": f"svc: {password},docs\n",
        "/tmp/docs-jaas.conf": (
            "ShapeDocs { org.eclipse.jetty.jaas.spi.PropertyFileLoginModule "
            'required file="/tmp/docs-users.properties"; };\n'
        ),
    }
    # docker cp also works if the registry exited while waiting for its JAAS file.
    archive = io.BytesIO()
    with tarfile.open(fileobj=archive, mode="w") as tar:
        for filename, content in files.items():
            data = content.encode()
            entry = tarfile.TarInfo(filename.rsplit("/", 1)[1])
            entry.mode = 0o600
            entry.uid = entry.gid = 1000
            entry.size = len(data)
            tar.addfile(entry, io.BytesIO(data))
    subprocess.run(["docker", "cp", "-", container + ":/tmp"], input=archive.getvalue(), check=True)
    subprocess.run(["docker", "restart", container], stdout=subprocess.DEVNULL, check=True)
    import base64

    basic = base64.b64encode(f"svc:{password}".encode()).decode()
    for attempt in range(60):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(
                    "http://localhost:8081/subjects", headers={"Authorization": "Basic " + basic}
                ),
                timeout=5,
            ) as response:
                assert response.status == 200
            try:
                urllib.request.urlopen("http://localhost:8081/subjects", timeout=5)
            except urllib.error.HTTPError as exc:
                assert exc.code == 401, f"Expected unauthenticated 401, got {exc.code}"
            else:
                raise AssertionError("Schema Registry accepted an unauthenticated request")
            print("Schema Registry: authenticated request 200, unauthenticated request 401")
            return
        except (urllib.error.URLError, TimeoutError):
            if attempt == 59:
                raise
            time.sleep(2)


if __name__ == "__main__":
    main()
