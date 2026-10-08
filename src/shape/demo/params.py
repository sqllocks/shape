"""``DemoParams``: the settings of one demo run, shared by every mode."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from shape.demo.errors import DemoError

DemoMode = Literal["inference", "streaming", "seeding"]
OutputFormat = Literal["terminal", "charts", "semantic_model", "all"]
ScaleMode = Literal["auto", "local", "spark"]

MODES = ("inference", "streaming", "seeding")
OUTPUT_FORMATS = ("terminal", "charts", "semantic_model", "all")
SCALE_MODES = ("auto", "local", "spark")


@dataclass
class DemoParams:
    """Unified parameter bag passed to every demo mode.

    ``rows`` picks the scale preset that is generated (``small`` up to 2,000, ``medium`` up to
    50,000, ``large`` up to 500,000, above that ``xlarge``); it is not an exact row count.
    ``output_dir`` is where files a run writes for you (charts, a semantic model) go; it defaults
    to the working directory.
    """

    scenario: str = "retail"
    mode: DemoMode = "inference"
    connection: str | None = None
    input_file: str | None = None
    db_schema: str = "dbo"
    db_tables: list[str] | None = None
    sample_rows: int = 1000
    rows: int = 100_000
    domain: str | None = None
    domains: list[str] | None = None
    output_formats: list[str] = field(default_factory=lambda: ["terminal"])
    env_name: str | None = None
    dry_run: bool = False
    estimate_only: bool = False
    auto_cleanup: bool = False
    seed: int | None = None
    scale_mode: ScaleMode = "auto"
    table_prefix: str | None = None
    output_dir: str | None = None
    max_events: int = 100
    #: The run switch of the identifier providers (``reserved`` or ``realistic``); None keeps
    #: the scenario's schema default, which is reserved for every shipped scenario.
    identifiers: str | None = None

    def validate(self) -> None:
        """Raise :class:`DemoError` for a setting no mode can use."""
        if self.mode not in MODES:
            raise DemoError(f"unknown mode {self.mode!r}; the modes are: {', '.join(MODES)}")
        if self.scale_mode not in SCALE_MODES:
            raise DemoError(
                f"unknown scale mode {self.scale_mode!r}; choose one of {', '.join(SCALE_MODES)}"
            )
        if int(self.rows) < 1:
            raise DemoError("rows must be at least 1")
        if int(self.sample_rows) < 1:
            raise DemoError("sample_rows must be at least 1")
        if int(self.max_events) < 1:
            raise DemoError("max_events must be at least 1")
        if self.identifiers is not None:
            from shape.generation.identifiers import check_identifiers

            try:
                check_identifiers(self.identifiers)
            except ValueError as exc:
                raise DemoError(str(exc)) from None
        bad = [f for f in self.output_formats if f not in OUTPUT_FORMATS]
        if bad:
            raise DemoError(
                f"unknown output format {', '.join(map(repr, bad))}; "
                f"choose from {', '.join(OUTPUT_FORMATS)}"
            )
