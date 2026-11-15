"""Fresh interpreter for notebook workloads; private pipe protocol, no secrets on disk.
The parent notebook owns authentication. Token/secret/prompt responses only traverse
stdin; stdout contains private protocol frames that the parent filters before display.
"""

import base64
import contextlib
import io
import json
import logging
import sys
import time
import types

WORKSPACE = "e90940bd-3f32-42ce-998e-ef2ddc74355b"
_PIPE_OUT = sys.stdout
_PIPE_IN = sys.stdin


def request(action, **fields):
    _PIPE_OUT.write(json.dumps({"rpc": action, **fields}) + "\n")
    _PIPE_OUT.flush()
    response = json.loads(_PIPE_IN.readline())
    if response.get("ok") is not True:
        raise RuntimeError("NotebookBridgeUnavailable")
    return response["value"]


def record(value):
    _PIPE_OUT.write(json.dumps({"record": value}) + "\n")
    _PIPE_OUT.flush()


@contextlib.contextmanager
def quiet():
    previous = logging.root.manager.disable
    try:
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            yield
    finally:
        logging.disable(previous)


class NotebookCredential:
    """Same notebook identity; refresh through the parent, never ambient SP auth."""

    def get_token(self, *scopes, **kwargs):
        permitted = {
            "https://analysis.windows.net/powerbi/api/.default",
            "https://api.fabric.microsoft.com/.default",
        }
        if not scopes or any(scope not in permitted for scope in scopes):
            raise RuntimeError("CredentialScopeNotAllowed")
        raw = request("token", audience="pbi")
        payload = raw.split(".")[1]
        expiry = int(
            json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["exp"]
        )
        if expiry <= time.time():
            raise RuntimeError("NotebookTokenExpired")
        from azure.core.credentials import AccessToken

        return AccessToken(raw, expiry)


def dependency_check(task):
    import importlib.metadata

    import numpy
    import pandas
    import pyarrow

    import shape

    versions = {
        "numpy": numpy.__version__,
        "pandas": pandas.__version__,
        "pyarrow": pyarrow.__version__,
    }
    matches = all(importlib.metadata.version(name) == value for name, value in versions.items())
    record(
        {
            "stage": "worker_runtime",
            "task": task,
            "fresh_interpreter": True,
            "python": sys.version.split()[0],
            "shape": shape.__version__,
            "loaded_versions": versions,
            "versions_match": matches,
        }
    )
    if not matches or numpy.__version__.split(".")[0] != "2" or shape.__version__ != "0.9.4":
        raise RuntimeError("FreshDependencyMismatch")


def main():
    envelope = json.loads(_PIPE_IN.readline())
    task = envelope["task"]
    config = envelope["config"]
    source = envelope["source"]
    if task not in ("after_install", "memory", "semantic_model", "events"):
        raise RuntimeError("WorkerTaskNotAllowed")
    credentials = types.SimpleNamespace(
        getToken=lambda audience: request("token", audience=audience),
        getSecret=lambda vault, name: (
            request("secret")
            if vault == config["F12_VAULT_URL"] and name == config["F12_SECRET_NAME"]
            else (_ for _ in ()).throw(RuntimeError("SecretReferenceMismatch"))
        ),
    )
    bridge = types.SimpleNamespace(
        credentials=credentials, notebook=types.SimpleNamespace(exit=lambda _: None)
    )
    namespace = {
        **config,
        "F12_INSTALL_OK": True,
        "_f12_record": record,
        "_f12_silent": quiet,
        "_f12_notebook_bridge": bridge,
        "contextlib": contextlib,
        "io": io,
    }
    try:
        with quiet(), contextlib.ExitStack() as stack:
            dependency_check(task)
            if task in ("semantic_model", "events"):
                # Set context/credential before importing sempy. No SP fixture is activated.
                from fabric.analytics.environment.context import SetFabricDefaultContext
                from fabric.analytics.environment.credentials import (
                    SetFabricAnalyticsDefaultTokenCredentials,
                )

                stack.enter_context(SetFabricDefaultContext(workspace_id=WORKSPACE))
                stack.enter_context(SetFabricAnalyticsDefaultTokenCredentials(NotebookCredential()))
            if task == "events":
                import getpass

                getpass.getpass = lambda label: request("prompt", label=label)
            exec(compile(source, "<f12-fresh-workload>", "exec"), namespace)
        return 0
    except BaseException as error:
        # pytest Failed is a BaseException; user cancellation still terminates this worker.
        record({"stage": task, "result": "failed", "error_type": type(error).__name__})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
