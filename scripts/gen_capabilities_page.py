"""Generate the capability reference from the release's installed registries."""

from __future__ import annotations

import argparse
import ast
import inspect
import re
import sys
import tomllib
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Display names only: membership, directions and schemes always come from registrations.
LABELS = {
    "sql": "SQL scripts",
    "abfss": "OneLake and ADLS Gen2",
    "onelake": "OneLake and ADLS Gen2",
    "mssql": "SQL Server",
    "sqlserver": "SQL Server",
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "duckdb": "DuckDB",
    "eventhubs": "Event Hubs",
    "eventhouse": "Eventhouse",
    "eventstream": "Eventstream",
    "ipc": "Arrow IPC",
    "jsonl": "JSONL",
    "csv": "CSV",
    "tsv": "TSV",
    "json": "JSON",
    "xml": "XML",
    "hl7v2": "HL7v2",
    "x12": "X12",
    "fhir": "FHIR",
    "omop": "OMOP",
    "ncpdp": "NCPDP",
    "dbt-seeds": "dbt seeds",
    "semantic-model": "Semantic model",
    "fabric-mirror": "Fabric mirror",
    "xlsx": "Excel",
}
GROUP_COMMAND = {
    "shape.sources": "profile",
    "shape.stream_sources": "stream-profile",
    "shape.sinks": "generate --to",
    "shape.emitters": "emit",
}


@dataclass(frozen=True)
class Capability:
    """One registered handler and the command that exposes it."""

    system: str
    name: str
    direction: str
    command: str
    schemes: tuple[str, ...]
    package: str
    extras: tuple[str, ...]
    extensions: tuple[str, ...] = ()
    exposed: bool = True

    @property
    def status(self) -> str:
        from shape.cli.stability import level

        return "experimental" if level(self.command.split()[0]) == "experimental" else "available"


def release_metadata() -> dict[str, dict]:
    """The core and plugin set shipped by this checkout, without a second plugin list."""
    paths = [ROOT / "pyproject.toml", *sorted((ROOT / "plugins").glob("*/pyproject.toml"))]
    return {p["name"]: p for path in paths for p in [tomllib.loads(path.read_text())["project"]]}


def install_extras(
    obj: object, project: dict, name: str, schemes: tuple[str, ...]
) -> tuple[str, ...]:
    """Extras named by the adapter's install hints or its package's matching extra."""
    declared = project.get("optional-dependencies", {})
    found = set(declared).intersection((name, *schemes))
    # The declaring module includes the adapter's lazy-driver error and INSTALL constants.
    module = inspect.getmodule(obj if inspect.isfunction(obj) else type(obj))
    text = inspect.getsource(module) if module is not None else ""
    if inspect.isfunction(obj):
        # A dispatcher module may also contain unrelated cloud install hints.
        text = inspect.getsource(obj)
        text += "\n" + repr(inspect.getdoc(module) or "")
    for package, extras in re.findall(r"(sqllocks-shape(?:-[\w-]+)?)\[([\w,.-]+)\]", text):
        if package == project["name"]:
            found.update(e for e in extras.split(",") if e in declared)
    for extra in re.findall(r"extra\s+`*\[([\w-]+)\]", text):
        if extra in declared:
            found.add(extra)
    for extra in re.findall(r"\[([\w-]+)\]`*\s+extra", text):
        if extra in declared:
            found.add(extra)
    for node in ast.walk(ast.parse(text)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "require"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value in declared
        ):
            found.add(node.args[1].value)
    return tuple(sorted(found))


def profile_file_formats() -> dict[str, tuple[tuple[str, ...], object]]:
    """The profiler's extension dispatch and workbook branch, with their real readers.

    The general IO reader's extension map is broader than shape profile's dispatch;
    it must not be used to invent CLI formats. CSV delimiter sniffing is performed
    by _read_files through _csv_options and sniff_delimiter.
    """
    from shape.io.excel import XLSX_SUFFIXES, read_workbook
    from shape.profile.reference import sources

    formats: dict[str, list[str]] = {}
    dispatch = ast.parse(inspect.getsource(sources._kind))
    suffixes = {
        node.value
        for node in ast.walk(dispatch)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(r"\.[a-z0-9]+", node.value)
    }
    for suffix in sorted(suffixes):
        kind = sources._kind(Path("capability" + suffix))
        formats.setdefault(kind, []).append(suffix)
    # profile() checks is_workbook_spec before its ordinary load_columns path.
    for suffix in XLSX_SUFFIXES:
        formats.setdefault("xlsx", []).append(suffix)
    return {
        kind: (tuple(sorted(extensions)), read_workbook if kind == "xlsx" else sources._read_files)
        for kind, extensions in formats.items()
    }


def capabilities() -> list[Capability]:
    """Walk the live host, restricted to installed distributions in the release set.

    Loading failures raise: silently omitting an adapter would publish an incomplete table.
    PluginRecord.status is deliberately not used as maturity metadata.
    """
    from shape.plugins.api.v1 import GROUPS
    from shape.plugins.host import PluginHost
    from shape.plugins.registry import register_builtins
    from shape.plugins.schemes import _KNOWN
    from shape.profile.reference.sources import _is_url
    from shape.streaming.file_source import FORMATS, FileStreamSource
    from shape.testdata.seed import SCRIPT_SCHEME, SINK_FOR_SCHEME

    projects = release_metadata()
    eps = {
        (ep.group, ep.name, ep.value): ep
        for dist in metadata.distributions()
        if dist.metadata["Name"] in projects
        for ep in dist.entry_points
        if ep.group in GROUPS
    }
    host = PluginHost(entry_points=lambda: eps.values(), project_config={})
    register_builtins(host)
    result = []
    file_formats = profile_file_formats()

    def add(obj: object, name: str, package: str, group: str) -> None:
        schemes = tuple(obj.schemes)
        if not schemes or not all(isinstance(s, str) and s for s in schemes):
            raise ValueError(f"{group}:{name} has no usable schemes")
        family = name.split("-", 1)[0]
        system = LABELS.get(
            family, _KNOWN.get(name, LABELS.get(name, name.replace("-", " ").capitalize()))
        )
        direction = "reads" if group in ("shape.sources", "shape.stream_sources") else "writes"
        extra = install_extras(obj, projects[package], name, schemes)
        exposed = True
        if group == "shape.sources":
            exposed = any(_is_url(f"{s}://capability") for s in schemes) or any(
                obj.can_open(f"file:///capability{suffix}")
                for extensions, _reader in file_formats.values()
                for suffix in extensions
            )
        commands = [GROUP_COMMAND[group]]
        if group == "shape.sinks":
            commands = [f"generate --format {name}"] if "file" in schemes else []
            if any(s != "file" for s in schemes):
                commands.append("generate --to")
            if any(SINK_FOR_SCHEME.get(s) == name for s in schemes) or name == "sql":
                commands.append("seed")
            if any(s != "file" for s in schemes):
                commands.append("emit")  # open_sink also routes registered table sinks.
        if group == "shape.stream_sources" and name == "file":
            schemes = (*schemes, "-")
        if group == "shape.sinks" and name == "sql":
            schemes = (*schemes, SCRIPT_SCHEME.removesuffix("://"))
        for command in commands:
            result.append(
                Capability(
                    system, name, direction, command, schemes, package, extra, exposed=exposed
                )
            )

    for group in GROUP_COMMAND:
        for record in host.records(group):
            if record.source in projects:
                add(host.get(group, record.name), record.name, record.source, group)
    for kind, (extensions, reader) in file_formats.items():
        result.append(
            Capability(
                LABELS.get(kind, kind.capitalize()),
                kind,
                "reads",
                "profile",
                ("file",),
                "sqllocks-shape",
                install_extras(reader, projects["sqllocks-shape"], kind, ("file",)),
                extensions,
            )
        )
    add(FileStreamSource(), "file", "sqllocks-shape", "shape.stream_sources")
    for fmt in FORMATS:
        add(FileStreamSource(), fmt, "sqllocks-shape", "shape.stream_sources")
    return sorted(result, key=lambda c: (c.system.casefold(), c.direction, c.command, c.package))


def planned_capabilities(path: Path | None = None) -> list[dict]:
    import yaml

    rows = yaml.safe_load((path or ROOT / "docs/reference/planned_capabilities.yml").read_text())
    for row in rows:
        if set(row) != {"system", "direction", "w9", "issue"} or row["direction"] not in (
            "reads",
            "writes",
        ):
            raise ValueError(f"invalid planned capability: {row}")
    return rows


def render(items: list[Capability] | None = None) -> str:
    items = capabilities() if items is None else items
    out = [
        "# Capabilities",
        "",
        "Generated from core and the installed release plugins' registrations at build time.",
        "Reads means profiling data; writes means generating, seeding or emitting data.",
        "Registrations without a route through those commands are shown as no.",
        "",
        "| System | Reads | Writes | URL scheme or command | Package | Install extra | Status |",
        "|---|---|---|---|---|---|---|",
    ]
    for system in sorted({c.system for c in items}, key=str.casefold):
        rows = [c for c in items if c.system == system]
        registered = rows
        rows = [c for c in rows if c.exposed]

        def direction(which: str, rows: list[Capability] = rows) -> str:
            commands = sorted({f"{c.command} ({c.status})" for c in rows if c.direction == which})
            return "yes" if commands else "no"

        statuses = []
        for which in ("reads", "writes"):
            commands = sorted({f"{c.command} ({c.status})" for c in rows if c.direction == which})
            if commands:
                statuses.append(f"{which.title()}: {', '.join(commands)}")
        statuses_text = "<br>".join(statuses) or "—"
        locations = []
        providers = []
        extras = []
        for which in ("reads", "writes"):
            handlers = [c for c in rows if c.direction == which]
            if not handlers:
                continue
            schemes = sorted({s for c in handlers for s in c.schemes})
            urls = ", ".join(
                f"`{s}://`" if s != "-" else "`shape stream-profile -`" for s in schemes
            )
            extensions = sorted({e for c in handlers for e in c.extensions})
            if extensions:
                urls += " (" + ", ".join(f"`{e}`" for e in extensions) + ")"
            locations.append(f"{which.title()}: {urls}")
            packages = sorted(
                {"core" if c.package == "sqllocks-shape" else c.package for c in handlers}
            )
            providers.append(f"{which.title()}: {', '.join(packages)}")
            installs = sorted(
                {
                    f"{c.package}[{','.join(c.extras)}]" if c.extras else f"{c.package}: none"
                    for c in handlers
                }
            )
            extras.append(f"{which.title()}: {'; '.join(installs)}")
        locations_text = "<br>".join(locations) or "—"
        packages_text = "<br>".join(providers) or ", ".join(
            sorted({"core" if c.package == "sqllocks-shape" else c.package for c in registered})
        )
        extras_text = "<br>".join(extras) or "; ".join(
            sorted(
                {
                    f"{c.package}[{','.join(c.extras)}]" if c.extras else f"{c.package}: none"
                    for c in registered
                }
            )
        )
        out.append(
            f"| {system} | {direction('reads')} | {direction('writes')} | "
            f"{locations_text} | {packages_text} | {extras_text} | {statuses_text} |"
        )
    out += [
        "",
        "Status follows the stability of the command that provides the capability "
        "(shape --help lists experimental commands).",
        "",
        "Install extras are taken from adapter install hints and package metadata; "
        "optional authentication or format extras may also be needed.",
        "",
        "## Planned",
        "",
    ]
    for row in planned_capabilities():
        if not any(
            c.exposed
            and c.system.casefold() == row["system"].casefold()
            and c.direction == row["direction"]
            for c in items
        ):
            out.append(
                f"- {row['system']} {row['direction']}: planned ({row['w9']}, [issue #{row['issue']}](https://github.com/sqllocks/shape/issues/{row['issue']}))."
            )
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "docs/reference/capabilities.md")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.check:
        return int(not args.out.is_file() or args.out.read_text() != text)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
