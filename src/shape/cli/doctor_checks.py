"""The network and sign-in checks of ``shape doctor``: Fabric access and broker reachability.

Each check is a :class:`Check` with a status (``pass``, ``warn``, ``fail``), a message that says
what was found and a ``next`` step. The checks take their network and credential as arguments, so
tests drive them with fakes; :class:`SocketNet` is the real one (sockets, ``ssl``, and HTTPS through
:func:`shape.scale.http.urllib_transport`).
Core holds no cloud SDK (T-18): a credential comes from ``shape.cli.auth.make_credential`` (the
``shape-fabric`` plugin), and Delta limits are read through ``deltalake`` when it is installed.
A token is used for one request and is never stored or printed.
"""

from __future__ import annotations

import socket
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import unquote, urlsplit

STATUSES = ("pass", "warn", "fail")
ONELAKE_HOST = "onelake.dfs.fabric.microsoft.com"
STORAGE_SCOPE = "https://storage.azure.com/.default"
EVENTHUBS_SCOPE = "https://eventhubs.azure.net/.default"
EVENTHUBS_DOMAIN = "servicebus.windows.net"
EVENTHUBS_AMQP_TLS_PORT = 5671
KAFKA_DEFAULT_PORT = 9092
DEFAULT_TIMEOUT = 5.0


@dataclass(frozen=True, slots=True)
class Check:
    """One line of the report."""

    id: str
    status: str
    message: str
    next: str = ""
    detail: str = ""  # what was checked (a host:port), for telling repeated checks apart

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"check status must be one of {STATUSES}, not {self.status!r}")

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "status": self.status, "message": self.message, "next": self.next}


def summarize(checks: list[Check]) -> dict[str, Any]:
    """``ok`` is False only when a check failed; a warning never fails the command."""
    return {"ok": all(c.status != "fail" for c in checks), "checks": [c.as_dict() for c in checks]}


class Net(Protocol):
    def resolve(self, host: str, port: int) -> list[tuple[int, str]]: ...
    def connect(self, address: tuple[int, str], port: int, timeout: float) -> None: ...
    def handshake(self, host: str, port: int, timeout: float) -> str: ...
    def http_get(self, url: str, headers: dict[str, str], timeout: float) -> int: ...


class SocketNet:
    """The real network."""

    def resolve(self, host: str, port: int) -> list[tuple[int, str]]:
        found: list[tuple[int, str]] = []
        for family, _t, _p, _c, sockaddr in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
            item = (int(family), str(sockaddr[0]))
            if item not in found:
                found.append(item)
        return found

    def connect(self, address: tuple[int, str], port: int, timeout: float) -> None:
        family, ip = address
        with socket.socket(family, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            s.connect((ip, port))

    def handshake(self, host: str, port: int, timeout: float) -> str:
        import ssl

        context = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                return tls.version() or "TLS"

    def http_get(self, url: str, headers: dict[str, str], timeout: float) -> int:
        # HTTP goes through the explicit fetch module (scripts/check_shipped_data.py)
        from shape.scale.http import urllib_transport

        return urllib_transport("GET", url, headers, None, timeout).status


def default_net() -> Net:
    return SocketNet()


# --- brokers -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BrokerTarget:
    kind: str  # "kafka" or "eventhubs"
    servers: list[str]
    tls: bool
    namespace: str = ""


def broker_target(uri: str) -> BrokerTarget:
    """The servers behind a ``kafka://host:port[,host:port]/topic`` or
    ``eventhubs://namespace/hub`` URI."""
    parts = urlsplit(uri)
    if parts.scheme == "kafka" and parts.netloc:
        return BrokerTarget("kafka", parts.netloc.split(","), tls=False)
    if parts.scheme == "eventhubs" and parts.netloc:
        ns = unquote(parts.netloc)
        host = ns if "." in ns else f"{ns}.{EVENTHUBS_DOMAIN}"
        return BrokerTarget(
            "eventhubs", [f"{host}:{EVENTHUBS_AMQP_TLS_PORT}"], tls=True, namespace=host
        )
    raise ValueError(
        f"not a broker URI: {uri!r} (kafka://host:9092[,host2:9092]/topic or "
        "eventhubs://<namespace>/<hub>)"
    )


def _split(server: str, default_port: int) -> tuple[str, int]:
    server = server.strip()
    if server.startswith("["):  # [v6]:port
        host, _, rest = server[1:].partition("]")
        return host, int(rest.lstrip(":") or default_port)
    host, sep, port = server.rpartition(":")
    if not sep:
        return server, default_port
    return host, int(port)


def _is_v6(address: tuple[int, str]) -> bool:
    return address[0] == socket.AF_INET6


def check_broker(
    servers: str | list[str],
    *,
    net: Net,
    tls: bool,
    default_port: int = KAFKA_DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Check]:
    """DNS, TCP and (when ``tls``) TLS for each server.

    A name that resolves to an IPv6 address that does not answer, while an IPv4 address does, is a
    warning that names the address; when only unreachable addresses are left it is a failure that
    says IPv6 may be the cause."""
    out: list[Check] = []
    items = servers.split(",") if isinstance(servers, str) else servers
    for server in items:
        host, port = _split(server, default_port)
        label = f"{host}:{port}"
        try:
            addresses = net.resolve(host, port)
        except OSError as exc:
            out.append(
                Check(
                    "broker.dns",
                    "fail",
                    f"{host} does not resolve ({exc})",
                    "check the name for typos, the network's DNS server and any private-link "
                    "DNS zone",
                    label,
                )
            )
            continue
        if not addresses:
            out.append(
                Check(
                    "broker.dns",
                    "fail",
                    f"{host} resolves to no address",
                    "check the name and the DNS server",
                    label,
                )
            )
            continue
        out.append(
            Check(
                "broker.dns",
                "pass",
                f"{host} resolves to {', '.join(a[1] for a in addresses)}",
                detail=label,
            )
        )
        reached: tuple[int, str] | None = None
        dead: list[tuple[tuple[int, str], str]] = []
        for address in addresses:
            try:
                net.connect(address, port, timeout)
            except OSError as exc:
                dead.append((address, str(exc) or type(exc).__name__))
                continue
            reached = address
            break
        dead_v6 = [a[1] for a, _ in dead if _is_v6(a)]
        if reached is None:
            why = "; ".join(f"{a[1]}: {e}" for a, e in dead)
            hint = ""
            msg = f"cannot connect to {label} ({why})"
            if dead_v6:
                msg += f"; {', '.join(dead_v6)} is an IPv6 address"
                hint = "the name may resolve to an IPv6 address this network cannot reach: "
            out.append(
                Check(
                    "broker.tcp",
                    "fail",
                    msg,
                    hint
                    + "check the port, the firewall and the network route to the broker; "
                    + (
                        "to prefer IPv4, set the address in the client or disable IPv6 on this host"
                        if dead_v6
                        else "make sure the broker is listening"
                    ),
                    label,
                )
            )
            continue
        if dead_v6:
            out.append(
                Check(
                    "broker.tcp",
                    "warn",
                    f"{label} is reachable over {reached[1]}, but the IPv6 address "
                    f"{', '.join(dead_v6)} (resolved first) did not answer",
                    "a client that tries IPv6 first will stall: prefer IPv4 (set the broker "
                    "address, or disable IPv6 on this host)",
                    label,
                )
            )
        else:
            out.append(
                Check("broker.tcp", "pass", f"connected to {label} ({reached[1]})", detail=label)
            )
        if tls:
            try:
                version = net.handshake(host, port, timeout)
            except OSError as exc:
                out.append(
                    Check(
                        "broker.tls",
                        "fail",
                        f"TLS to {label} failed ({exc})",
                        "check the certificate chain, a TLS-inspecting proxy and the system clock",
                        label,
                    )
                )
            else:
                out.append(
                    Check("broker.tls", "pass", f"TLS to {label} works ({version})", detail=label)
                )
    return out


def check_broker_auth(
    target: BrokerTarget,
    *,
    credential: Any = None,
    probe: Callable[[list[str]], None] | None = None,
) -> Check:
    """Sign in to the broker. Event Hubs: a token for the namespace from ``credential``. Kafka:
    ``probe(servers)`` (a metadata request through the client library; raises when it fails), or
    a warning when no client library is available to try it."""
    if target.kind == "eventhubs":
        if credential is None:
            return Check(
                "broker.auth",
                "warn",
                "no credential to try",
                "pass --auth, or rely on SHAPE_EVENTHUBS_CONNECTION_STRING",
            )
        try:
            credential.get_token(EVENTHUBS_SCOPE)
        except Exception as exc:  # any sign-in failure is a finding, not a crash
            return Check(
                "broker.auth",
                "fail",
                f"sign-in for Event Hubs failed ({_brief(exc)})",
                "run `az login`, or choose another --auth mode",
            )
        return Check("broker.auth", "pass", f"signed in for {target.namespace}")
    if probe is None:
        return Check(
            "broker.auth",
            "warn",
            "authentication was not tried (the confluent-kafka package is not installed)",
            "pip install sqllocks-shape-kafka",
        )
    try:
        probe(target.servers)
    except Exception as exc:
        return Check(
            "broker.auth",
            "fail",
            f"the broker refused the sign-in ({_brief(exc)})",
            "check the SASL user, password and mechanism in the client configuration",
        )
    return Check("broker.auth", "pass", "the broker answered a metadata request")


def kafka_probe() -> Callable[[list[str]], None] | None:
    """A metadata request through ``confluent_kafka``, or ``None`` when it is not installed."""
    import importlib

    try:
        admin = importlib.import_module("confluent_kafka.admin")
    except ImportError:
        return None

    def probe(servers: list[str]) -> None:
        admin.AdminClient({"bootstrap.servers": ",".join(servers)}).list_topics(
            timeout=DEFAULT_TIMEOUT
        )

    return probe


def _brief(exc: BaseException) -> str:
    text = " ".join(str(exc).split()) or type(exc).__name__
    return text if len(text) <= 160 else text[:157] + "..."


# --- Fabric ------------------------------------------------------------------------------


def check_fabric_endpoint(net: Net, *, timeout: float = DEFAULT_TIMEOUT) -> list[Check]:
    """DNS, TCP and TLS to the OneLake endpoint."""
    return check_broker(f"{ONELAKE_HOST}:443", net=net, tls=True, timeout=timeout)


def check_fabric(
    target: str,
    *,
    net: Net,
    credential: Any,
    auth_mode: str = "cli",
    timeout: float = DEFAULT_TIMEOUT,
) -> list[Check]:
    """OneLake reachable, sign-in works, and the workspace and item exist."""
    from urllib.parse import quote

    ws, item = _fabric_target(target)
    reach = check_fabric_endpoint(net, timeout=timeout)
    out = [Check("fabric.onelake", c.status, c.message, c.next, c.detail) for c in reach]
    if any(c.status == "fail" for c in reach):
        return out
    try:
        token = credential.get_token(STORAGE_SCOPE).token
    except Exception as exc:
        out.append(
            Check(
                "fabric.auth",
                "fail",
                f"sign-in with --auth {auth_mode} failed ({_brief(exc)})",
                _auth_hint(auth_mode),
            )
        )
        return out
    out.append(Check("fabric.auth", "pass", f"signed in with --auth {auth_mode}"))
    url = (
        f"https://{ONELAKE_HOST}/{quote(ws, safe='')}?resource=filesystem&recursive=false"
        f"&directory={quote(item, safe='')}&maxResults=1"
    )
    try:
        code = net.http_get(url, {"Authorization": f"Bearer {token}"}, timeout)
    except OSError as exc:
        out.append(
            Check(
                "fabric.item",
                "warn",
                f"could not ask OneLake about {ws}/{item} ({_brief(exc)})",
                "retry; check the network",
            )
        )
        return out
    out.append(_item_check(code, ws, item))
    return out


def _item_check(code: int, ws: str, item: str) -> Check:
    what = f"{ws}/{item}"
    if 200 <= code < 300:
        return Check("fabric.item", "pass", f"workspace and item exist ({what})")
    if code == 404:
        return Check(
            "fabric.item",
            "fail",
            f"workspace or item not found ({what})",
            "check the workspace name or GUID and the item name (name.Lakehouse or GUID)",
        )
    if code == 403:
        return Check(
            "fabric.item",
            "fail",
            f"no permission on {what}",
            "ask a workspace admin for Contributor (or higher) on the workspace, and check "
            "that the tenant allows OneLake access from outside Fabric",
        )
    if code == 401:
        return Check(
            "fabric.item",
            "fail",
            f"OneLake rejected the token for {what}",
            "sign in again; for a service principal, check the tenant and that it may "
            "use Fabric APIs",
        )
    return Check(
        "fabric.item",
        "warn",
        f"OneLake answered HTTP {code} for {what}",
        "retry later; if it persists, check the Fabric service status",
    )


def _auth_hint(mode: str) -> str:
    return {
        "cli": "run `az login`, or choose another --auth mode",
        "spn": "check --tenant-id, --client-id and the secret behind --client-secret",
        "msi": "run on a host with a managed identity, or pass --client-id for a user-assigned one",
        "device-code": "repeat and finish the browser sign-in",
        "fabric": "--auth fabric works inside a Fabric notebook only",
        "sql": "--auth sql signs in to a SQL endpoint; use cli or spn for OneLake",
    }.get(mode, "check the sign-in options")


def _fabric_target(target: str) -> tuple[str, str]:
    """``(workspace, item)`` of an ``onelake://workspace/item`` or OneLake ``abfss://`` URI."""
    parts = urlsplit(target)
    segs = [unquote(s) for s in parts.path.split("/") if s]
    if parts.scheme == "onelake" and parts.netloc and segs:
        item = segs[0]
        if (
            not item.endswith((".Lakehouse", ".Warehouse", ".SQLDatabase", ".KQLDatabase"))
            and "-" not in item
        ):
            item += ".Lakehouse"
        return parts.netloc, item
    if parts.scheme in ("abfss", "abfs") and parts.netloc.endswith("@" + ONELAKE_HOST) and segs:
        return parts.netloc.split("@", 1)[0], segs[0]
    raise ValueError(
        f"not a OneLake target: {target!r} (onelake://<workspace>/<item> or "
        f"abfss://<workspace>@{ONELAKE_HOST}/<item>)"
    )


# --- delta-rs limits ---------------------------------------------------------------------


def delta_features(path: str) -> dict[str, Any]:
    """The reader features and table properties of the Delta table at ``path``."""
    from deltalake import DeltaTable

    table = DeltaTable(path)
    protocol = table.protocol()
    features = list(getattr(protocol, "reader_features", None) or [])
    return {
        "reader_features": [str(f) for f in features],
        "configuration": dict(table.metadata().configuration or {}),
    }


def delta_version() -> str | None:
    try:
        import importlib.metadata as md

        return md.version("deltalake")
    except Exception:
        return None


def check_delta_limits(
    reader: Callable[[], dict[str, Any]] | None, *, version: str | None
) -> Check:
    """The delta-rs limits that apply in a Python notebook: deletion vectors and column mapping
    cannot be read by the delta-rs reader Shape uses."""
    if version is None or reader is None:
        return Check(
            "delta.limits",
            "warn",
            "deltalake is not installed, so Delta tables cannot be checked",
            "pip install 'sqllocks-shape[azure]'",
        )
    try:
        found = reader()
    except Exception as exc:
        return Check(
            "delta.limits",
            "warn",
            f"could not read the Delta table ({_brief(exc)})",
            "check the path and the sign-in",
        )
    limits: list[str] = []
    feats = {f.lower() for f in found.get("reader_features", [])}
    conf = found.get("configuration", {})
    if (
        "deletionvectors" in feats
        or str(conf.get("delta.enableDeletionVectors", "")).lower() == "true"
    ):
        limits.append("deletion vectors")
    if "columnmapping" in feats or str(
        conf.get("delta.columnMapping.mode", "none")
    ).lower() not in ("none", ""):
        limits.append("column mapping")
    if limits:
        return Check(
            "delta.limits",
            "fail",
            f"the table uses {' and '.join(limits)}, which delta-rs {version} cannot read in a "
            "Python notebook",
            "read it from Spark or a SQL endpoint, or rewrite the table without these features "
            "(for example `REORG TABLE ... APPLY (PURGE)` and `ALTER TABLE ... SET TBLPROPERTIES "
            "('delta.enableDeletionVectors' = false)`)",
        )
    return Check(
        "delta.limits", "pass", f"no deletion vectors or column mapping (delta-rs {version})"
    )
