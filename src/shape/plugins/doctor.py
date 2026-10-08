"""``shape plugins doctor``: load every plugin and report which ones failed and why."""

from __future__ import annotations

from typing import Any

from shape.plugins.host import HOST_API, PluginHost
from shape.plugins.trust import CHECK_FAILED


def diagnose(host: PluginHost) -> dict[str, Any]:
    """Load every plugin on ``host`` and summarise.

    ``ok`` is true when none failed. Each entry of ``plugins`` is ``PluginRecord.as_dict()``.
    Plugins the allow-list refused are in ``blocked`` instead (with a ``kind`` of
    ``"not_permitted"`` or ``"check_failed"``) and were not imported. A plugin that is merely
    not permitted does not make ``ok`` false; a listed plugin that fails its version, file or
    signature check does.
    """
    records = host.load_all()
    blocked = [{**r.as_dict(), "kind": r.blocked_kind} for r in records if r.status == "blocked"]
    shown = [r for r in records if r.status != "blocked"]
    return {
        "api": HOST_API,
        "ok": all(r.status == "ok" for r in shown)
        and not any(b["kind"] == CHECK_FAILED for b in blocked),
        "plugins": [r.as_dict() for r in shown],
        "blocked": blocked,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [f"plugin API {report['api']}: {len(report['plugins'])} plugin(s)"]
    for p in report["plugins"]:
        tail = f" -- {p['error']}" if p["error"] else ""
        lines.append(f"  {p['status']:<5} {p['group']}:{p['name']} [{p['source']}]{tail}")
    if report.get("blocked"):
        lines.append(f"blocked by the plugin allow-list (not imported): {len(report['blocked'])}")
        for b in report["blocked"]:
            lines.append(f"  {b['group']}:{b['name']} [{b['source']}] -- {b['error']}")
    lines.append("all plugins load" if report["ok"] else "some plugins failed to load")
    return "\n".join(lines)
