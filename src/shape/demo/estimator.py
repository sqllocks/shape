"""``CostEstimator``: estimate the Fabric capacity a demo needs before it runs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostEstimate:
    rows: int
    targets: list[str]
    estimated_cu_minutes: float
    estimated_duration_seconds: float
    warning: str = ""

    def __str__(self) -> str:
        mins = self.estimated_duration_seconds / 60
        return (
            f"  Rows to generate: {self.rows:,}\n"
            f"  Targets: {', '.join(self.targets)}\n"
            f"  Estimated CU-minutes: {self.estimated_cu_minutes:.1f}\n"
            f"  Estimated duration: ~{mins:.1f} min\n"
            + (f"  WARNING: {self.warning}\n" if self.warning else "")
        )


class CostEstimator:
    _CU_PER_M_ROWS = {
        "warehouse": 2.5,
        "lakehouse": 1.8,
        "sql_db": 3.0,
        "eventhouse": 1.2,
        "local": 0.0,
        "generated": 0.0,
    }
    _SECONDS_PER_M_ROWS = {
        "warehouse": 45,
        "lakehouse": 30,
        "sql_db": 60,
        "eventhouse": 20,
        "local": 10,
        "generated": 10,
    }

    def estimate(self, rows: int, targets: list[str]) -> CostEstimate:
        m_rows = rows / 1_000_000
        cu = sum(self._CU_PER_M_ROWS.get(t, 2.0) * m_rows for t in targets)
        # Targets are written one after another: the duration is the sum of the per-target costs.
        secs = sum(self._SECONDS_PER_M_ROWS.get(t, 45) * m_rows for t in targets)
        if not targets:
            secs = 10.0
        secs = max(secs, 10)
        warning = ""
        if cu > 50:
            warning = "High CU usage — consider running during off-peak hours."
        elif rows > 10_000_000:
            warning = "Large row count — use the chunked scale router for memory efficiency."
        return CostEstimate(
            rows=rows,
            targets=targets,
            estimated_cu_minutes=round(cu, 1),
            estimated_duration_seconds=round(secs),
            warning=warning,
        )
