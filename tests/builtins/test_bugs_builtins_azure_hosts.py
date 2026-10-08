"""#276: abfss:// hosts are allow-listed before any credential is attached."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from shape.builtins.sinks import AbfssSink
from shape.builtins.sources import azure

GOOD_HOSTS = [
    "acct.dfs.core.windows.net",
    "acct.blob.core.windows.net",
    "ACCT.DFS.CORE.WINDOWS.NET",
    "onelake.dfs.fabric.microsoft.com",
    "onelake.blob.fabric.microsoft.com",
    "westus-onelake.dfs.fabric.microsoft.com",
    "acct.dfs.core.usgovcloudapi.net",
    "acct.blob.core.usgovcloudapi.net",
    "acct.dfs.core.chinacloudapi.cn",
    "acct.blob.core.chinacloudapi.cn",
]
BAD_HOSTS = [
    "attacker.example.net",
    "dfs.core.windows.net",  # no account label
    "evil.com.dfs.core.windows.net.attacker.net",
    "attacker.net/.dfs.core.windows.net",
    "acct.dfs.core.windows.net.",
    "acct.dfs.core.windows.net:8080",
    "x@acct.dfs.core.windows.net",
    "a.b.dfs.core.windows.net",
    "xdfs.core.windows.net",
    "onelake.dfs.fabric.microsoft.com.evil.net",
    "evilonelake.dfs.fabric.microsoft.com.x",
    "localhost",
    "",
]


@pytest.mark.parametrize("host", GOOD_HOSTS)
def test_known_storage_hosts_are_accepted(host: str) -> None:
    assert azure.parse(f"abfss://c@{host}/p").host == host


@pytest.mark.parametrize("host", [h for h in BAD_HOSTS if h and "/" not in h])
def test_other_hosts_are_refused_by_parse(host: str) -> None:
    with pytest.raises(ValueError, match="not an Azure Storage or OneLake host"):
        azure.parse(f"abfss://c@{host}/p")


def test_short_form_without_host_still_parses() -> None:
    assert azure.parse("abfss://c/p").host is None


@pytest.fixture
def stub_adlfs(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []

    class FS:
        def __init__(self, **kwargs: Any) -> None:
            calls.append(kwargs)

    module = types.ModuleType("adlfs")
    module.AzureBlobFileSystem = FS  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adlfs", module)
    return calls


def test_sink_refuses_foreign_host_before_attaching_the_environment_secret(
    monkeypatch: pytest.MonkeyPatch, stub_adlfs: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=2024&sig=USER_SAS_SECRET")
    with pytest.raises(ValueError, match="not an Azure Storage or OneLake host") as info:
        AbfssSink().write("abfss://raw@attacker.example.net/x", "t", [])
    assert "USER_SAS_SECRET" not in str(info.value)
    assert stub_adlfs == []


def test_source_filesystem_refuses_foreign_host(stub_adlfs: list[dict[str, Any]]) -> None:
    loc = azure.Location("c", "attacker.example.net", "x")
    with pytest.raises(ValueError, match="not an Azure Storage or OneLake host"):
        azure._filesystem(loc, {"account_key": "k"})
    assert stub_adlfs == []


def test_good_host_still_reaches_adlfs(
    monkeypatch: pytest.MonkeyPatch, stub_adlfs: list[dict[str, Any]]
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=2024&sig=S")
    AbfssSink()._store("abfss://raw@acct.dfs.core.windows.net/x", {})
    assert stub_adlfs[0]["account_name"] == "acct"
    assert stub_adlfs[0]["sas_token"] == "sv=2024&sig=S"
