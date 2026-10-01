"""``shape plugins doctor``: load every plugin and report which ones failed and why."""

from __future__ import annotations

from typing import Any

from shape.plugins.host import HOST_API, PluginHost


def diagnose(host: PluginHost) -> dict[str, Any]:
    """Load every plugin on ``host`` and summarise.

    ``ok`` is true when none failed. Each entry of ``plugins`` is ``PluginRecord.as_dict()``.
    """
    records = host.load_all()
    return {
        "api": HOST_API,
        "ok": all(r.status == "ok" for r in records),
        "plugins": [r.as_dict() for r in records],
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [f"plugin API {report['api']}: {len(report['plugins'])} plugin(s)"]
    for p in report["plugins"]:
        tail = f" -- {p['error']}" if p["error"] else ""
        lines.append(f"  {p['status']:<5} {p['group']}:{p['name']} [{p['source']}]{tail}")
    lines.append("all plugins load" if report["ok"] else "some plugins failed to load")
    return "\n".join(lines)
