"""``shape doctor``: what this installation can and cannot do, and why."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import json
import platform
import sys
from typing import Any

from shape.cli import doctor_checks as dc
from shape.cli.doctor_checks import Check

# (import name, distribution name, what needs it)
REQUIRED = (
    ("numpy", "numpy", "everything"),
    ("pyarrow", "pyarrow", "reading and profiling data"),
)
OPTIONAL = (
    ("cryptography", "cryptography", "signing and verifying artifacts (--sign, keygen, verify)"),
    ("yaml", "pyyaml", "YAML generation schemas and scenario packs"),
    ("pandas", "pandas", "profiling a pandas DataFrame"),
    ("scipy", "scipy", "the statistical tests of `shape verify --statistical`"),
    ("deltalake", "deltalake", "reading and writing Delta tables"),
    ("openpyxl", "openpyxl", "Excel output (`shape generate --format excel`)"),
    ("sklearn", "scikit-learn", "fidelity tiers 2 and 3 (`shape fidelity --tier`)"),
    ("tzdata", "tzdata", "time zones on a system without a time-zone database (Windows)"),
)


def _installed(module: str, dist: str) -> str | None:
    """The installed version, or None when the package is not importable."""
    try:
        if importlib.util.find_spec(module) is None:
            return None
    except (ImportError, ValueError):
        return None
    try:
        return importlib.metadata.version(dist)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def report() -> dict[str, Any]:
    """The facts, as a dictionary: ``ok`` is False when a required package is missing."""
    from shape import __version__
    from shape.kernel import kernel_name

    required = {m: _installed(m, d) for m, d, _ in REQUIRED}
    optional = {m: _installed(m, d) for m, d, _ in OPTIONAL}
    py_ok = sys.version_info >= (3, 11)
    return {
        "shape": __version__,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "kernel": kernel_name(),
        "required": required,
        "optional": optional,
        "checks": [],
        "ok": py_ok and all(v is not None for v in required.values()),
        # flat, for scripts that read the earlier output
        **{
            m: v
            for m, v in {**required, **optional}.items()
            if m in ("pyarrow", "cryptography", "yaml")
        },
    }


def render(rep: dict[str, Any]) -> str:
    lines = [
        f"Shape {rep['shape']}",
        f"  python    {rep['python']}  ({rep['platform']})",
        f"  kernel    {rep['kernel']}"
        + ("  (compiled)" if rep["kernel"] == "rust" else "  (pure Python; set SHAPE_KERNEL=rust)"),
        "",
        "Required",
    ]
    for m, _d, why in REQUIRED:
        v = rep["required"][m]
        lines.append(
            f"  {'OK' if v else 'MISSING':<8}{m:<14}{v or 'not installed (needed for ' + why + ')'}"
        )
    lines += ["", "Optional"]
    for m, _d, why in OPTIONAL:
        v = rep["optional"][m]
        lines.append(f"  {'OK' if v else 'missing':<8}{m:<14}{v or 'needed for ' + why}")
    if rep["checks"]:
        lines += ["", "Checks"]
        for c in rep["checks"]:
            lines.append(f"  {c['status'].upper():<5} {c['id']:<14}{c['message']}")
            if c["next"] and c["status"] != "pass":
                lines.append(f"        Next: {c['next']}")
    lines += ["", "Result: " + ("OK" if rep["ok"] else "FAILED (" + _why(rep) + ")")]
    return "\n".join(lines)


def _why(rep: dict[str, Any]) -> str:
    bad = [c["id"] for c in rep["checks"] if c["status"] == "fail"]
    if any(v is None for v in rep["required"].values()):
        return "a required package is missing"
    return "failed check: " + ", ".join(bad) if bad else "Python 3.11 or newer is required"


def add_arguments(parser: Any) -> None:
    from shape.cli import auth

    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    g = parser.add_argument_group("connectivity checks (each is off unless asked for)")
    g.add_argument(
        "--fabric",
        metavar="URI",
        help="check Fabric access: onelake://WORKSPACE/ITEM (OneLake reachable, sign-in, "
        "workspace and item exist)",
    )
    g.add_argument(
        "--broker",
        action="append",
        default=[],
        metavar="URI",
        help="check a broker: kafka://host:9092[,host2:9092]/topic or eventhubs://NAMESPACE/HUB "
        "(DNS, TCP, TLS, sign-in; repeatable)",
    )
    g.add_argument(
        "--delta-table",
        metavar="PATH",
        help="check a Delta table for deletion vectors and column mapping, which delta-rs cannot "
        "read in a Python notebook",
    )
    g.add_argument("--timeout", type=float, default=5.0, metavar="SECONDS", help="per network step")
    g.add_argument(
        "--no-auth-check", action="store_true", help="skip the sign-in step of --broker checks"
    )
    auth.add_arguments(parser, connection_string=False)


def _credential(settings: dict[str, str] | None) -> tuple[Any, str, Check | None]:
    from shape.cli import auth

    mode = (settings or {}).get("mode", "cli")
    try:
        return auth.make_credential(settings or {"mode": mode}), mode, None
    except Exception as exc:  # the plugin missing, or a bad setting: a finding, not a crash
        return (
            None,
            mode,
            Check(
                "fabric.auth",
                "fail",
                f"cannot prepare sign-in with --auth {mode} ({exc})",
                "install the shape-fabric plugin (pip install 'sqllocks-shape[fabric]') or fix the "
                "--auth options",
            ),
        )


def connectivity(a: Any) -> list[Check]:
    """The checks the command line asked for."""
    from shape.cli import auth

    settings = auth.settings_from_args(a)
    fabric = getattr(a, "fabric", None)
    brokers = [dc.broker_target(u) for u in getattr(a, "broker", None) or []]
    delta = getattr(a, "delta_table", None)
    out: list[Check] = []
    net = dc.default_net()
    timeout = getattr(a, "timeout", dc.DEFAULT_TIMEOUT)
    if fabric:
        dc._fabric_target(fabric)  # a bad URI is an input error before any network use
        cred, mode, failed = _credential(settings)
        if failed:
            out += [failed]
        else:
            out += dc.check_fabric(
                fabric, net=net, credential=cred, auth_mode=mode, timeout=timeout
            )
    for target in brokers:
        servers = target.servers
        out += dc.check_broker(servers, net=net, tls=target.tls, timeout=timeout)
        reachable = all(
            c.status != "fail" for c in out if c.id.startswith("broker.") and c.detail in servers
        )
        if getattr(a, "no_auth_check", False) or not reachable:
            continue
        if target.kind == "eventhubs":
            cred, mode, failed = _credential(settings)
            out.append(
                Check("broker.auth", failed.status, failed.message, failed.next)
                if failed
                else dc.check_broker_auth(target, credential=cred)
            )
        else:
            out.append(dc.check_broker_auth(target, probe=dc.kafka_probe()))
    if delta:
        version = dc.delta_version()
        out.append(
            dc.check_delta_limits(
                (lambda: dc.delta_features(delta)) if version else None, version=version
            )
        )
    return out


def run(a: Any) -> int:
    from shape.cli import errors

    rep = report()
    try:
        rep["checks"] = [c.as_dict() for c in connectivity(a)]
    except ValueError as exc:
        return errors.fail(exc)
    rep["ok"] = rep["ok"] and all(c["status"] != "fail" for c in rep["checks"])
    if a.json:
        print(json.dumps(rep, sort_keys=True))
    else:
        print(render(rep))
    return 0 if rep["ok"] else 1
