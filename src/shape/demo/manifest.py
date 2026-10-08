"""``DemoManifest``: a record of every artifact a demo session created."""

from __future__ import annotations

import html
import json
import os
import tempfile
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shape.demo.errors import DemoError, SessionNotFoundError
from shape.demo.home import check_name, sessions_dir


def _now() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


@dataclass
class ArtifactRecord:
    target: str
    name: str
    row_count: int = 0
    detail: str = ""


@dataclass
class DemoManifest:
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    scenario: str = ""
    mode: str = ""
    started_at: str = field(default_factory=_now)
    finished_at: str | None = None
    success: bool = False
    error: str | None = None
    artifacts: list[ArtifactRecord] = field(default_factory=list)
    params: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    scale_mode: str | None = None
    fabric_run_id: str | None = None
    workspace_id: str | None = None
    notebook_item_id: str | None = None
    _path: Path | None = field(default=None, repr=False, compare=False)

    def add_artifact(self, target: str, name: str, row_count: int = 0, detail: str = "") -> None:
        self.artifacts.append(
            ArtifactRecord(target=target, name=name, row_count=row_count, detail=detail)
        )

    def finish(self, success: bool, error: str | None = None) -> None:
        self.finished_at = _now()
        self.success = success
        self.error = error

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("_path", None)
        return d

    def save(self, directory: Path | None = None) -> Path:
        check_name(self.session_id, "session id")
        dir_ = directory or sessions_dir()
        dir_.mkdir(parents=True, exist_ok=True)
        path = dir_ / f"demo-{self.session_id}.json"
        text = json.dumps(self.to_dict(), indent=2)
        fd, tmp = tempfile.mkstemp(dir=dir_, prefix=".demo-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        self._path = path
        return path

    def take_free_id(self, directory: Path | None = None) -> None:
        """Draw a new id while this one already has a saved record (call before anything is
        written: the id is part of the folder and table names a run creates)."""
        dir_ = directory or sessions_dir()
        while (dir_ / f"demo-{self.session_id}.json").exists():
            self.session_id = str(uuid.uuid4())[:8]

    @classmethod
    def load(cls, session_id: str, directory: Path | None = None) -> DemoManifest:
        try:
            check_name(session_id, "session id")
        except ValueError as exc:
            raise SessionNotFoundError(str(exc)) from None
        dir_ = directory or sessions_dir()
        path = dir_ / f"demo-{session_id}.json"
        if not path.exists():
            raise SessionNotFoundError(f"no session {session_id!r} found in {dir_}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise TypeError(f"it holds {type(data).__name__}, not an object")
            # a field a later release added is ignored (docs/specs/STATE_AND_COMPATIBILITY.md)
            artifact_fields = {f.name for f in fields(ArtifactRecord)}
            artifacts = [
                ArtifactRecord(**{k: v for k, v in a.items() if k in artifact_fields})
                for a in data.pop("artifacts", [])
            ]
            known = {f.name for f in fields(cls) if not f.name.startswith("_")}
            manifest = cls(**{k: v for k, v in data.items() if k in known})
        except (ValueError, TypeError, AttributeError) as exc:
            raise DemoError(f"{path} is not a demo session record: {exc}") from exc
        manifest.artifacts = artifacts
        manifest._path = path
        return manifest

    def export(self, format: str = "md") -> str:
        total_rows = sum(a.row_count for a in self.artifacts)
        status = "SUCCESS" if self.success else f"FAILED: {self.error or 'unknown'}"
        targets = sorted({a.target for a in self.artifacts})

        if format == "md":
            lines = [
                f"# Shape Demo Report — Session {self.session_id}",
                "",
                "| Field | Value |",
                "|---|---|",
                f"| Scenario | {_cell(self.scenario)} |",
                f"| Mode | {_cell(self.mode)} |",
                f"| Status | {_cell(status)} |",
                f"| Started | {self.started_at} |",
                f"| Finished | {self.finished_at or 'running'} |",
                f"| Total rows | {total_rows:,} |",
                f"| Targets | {_cell(', '.join(targets))} |",
                "",
                "## Artifacts Created",
                "",
                "| Target | Name | Rows |",
                "|---|---|---|",
            ]
            for a in self.artifacts:
                lines.append(f"| {_cell(a.target)} | {_cell(a.name)} | {a.row_count:,} |")
            if self.metrics:
                lines += ["", "## Metrics", ""]
                for k, v in self.metrics.items():
                    lines.append(f"- **{_cell(str(k))}**: {_cell(str(v))}")
            return "\n".join(lines)

        if format == "html":
            e = html.escape
            rows_html = "\n".join(
                f"<tr><td>{e(a.target)}</td><td>{e(a.name)}</td><td>{a.row_count:,}</td></tr>"
                for a in self.artifacts
            )
            return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Shape Demo — {e(self.session_id)}</title>
<style>{_STYLE}</style>
</head><body>
<h1>Shape Demo Report — {e(self.session_id)}</h1>
<p>Scenario: <strong>{e(self.scenario)}</strong> | Mode: <strong>{e(self.mode)}</strong> \
| Status: <strong>{e(status)}</strong></p>
<p>Total rows: <strong>{total_rows:,}</strong></p>
<h2>Artifacts</h2>
<table><tr><th>Target</th><th>Name</th><th>Rows</th></tr>{rows_html}</table>
</body></html>"""
        raise ValueError(f"unknown format: {format!r}. Use 'md' or 'html'.")


_STYLE = (
    "body{font-family:sans-serif;padding:24px} table{border-collapse:collapse;width:100%} "
    "td,th{border:1px solid #ddd;padding:8px}"
)


def _cell(text: str) -> str:
    """``text`` made safe for a Markdown table cell: a ``|`` or a line break would end the cell."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")
