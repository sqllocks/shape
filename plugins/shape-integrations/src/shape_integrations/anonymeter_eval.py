"""Anonymeter privacy-risk report (imports Anonymeter: needs the ``anonymeter`` extra).

Per table: singling out (multivariate queries of up to three columns, fixed seed), linkability (the columns split in
two halves in table order, ten neighbours at most) and inference (each column in turn as the
secret, the others as what the attacker knows). ``n_attacks`` is 500, or fewer when the real or
control table has fewer rows. Singling out is seeded; the other two sample attacks without a
seed in Anonymeter, so their rates can differ a little between runs.
"""

from __future__ import annotations

import logging
import warnings
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .evaluate import EvaluateInputError
from .extras import require

SHAPE_API = "1.0"

N_ATTACKS = 500
MIN_ROWS = 2
SEED = 20261003
ATTEMPTS_PER_ATTACK = 100
MAX_NEIGHBORS = 10


def _supported(t: pa.DataType) -> bool:
    return not (
        pa.types.is_list(t)
        or pa.types.is_large_list(t)
        or pa.types.is_struct(t)
        or pa.types.is_map(t)
        or pa.types.is_binary(t)
        or pa.types.is_large_binary(t)
        or pa.types.is_fixed_size_binary(t)
        or pa.types.is_null(t)
    )


def _rate(r: Any) -> dict[str, float]:
    return {"value": float(r.value), "error": float(r.error)}


def _outcome(res: Any, n_attacks: int, notes: list[str]) -> dict[str, Any]:
    risk = res.risk()
    out: dict[str, Any] = {
        "n_attacks": n_attacks,
        "n_success": int(res.n_success),
        "n_baseline": int(res.n_baseline),
        "attack_rate": _rate(res.attack_rate),
        "baseline_rate": _rate(res.baseline_rate),
        "risk": {"value": float(risk.value), "ci": [float(risk.ci[0]), float(risk.ci[1])]},
    }
    if res.control_rate is not None:
        out["n_control"] = int(res.n_control)
        out["control_rate"] = _rate(res.control_rate)
    if notes:
        out["notes"] = notes
    return out


class _Notes(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def _collect(run: Any) -> tuple[Any, list[str]]:
    """Call ``run()`` and return its result with Anonymeter's own warnings as notes."""
    handler = _Notes()
    logger = logging.getLogger("anonymeter")
    logger.addHandler(handler)
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            result = run()
    finally:
        logger.removeHandler(handler)
    notes = [str(w.message) for w in caught if "anonymeter" in str(w.filename)] + handler.messages
    return result, sorted(set(notes))


def _table_results(real: Any, synth: Any, control: Any, attacks: list[str]) -> dict[str, Any]:
    from anonymeter.evaluators import (
        InferenceEvaluator,
        LinkabilityEvaluator,
        SinglingOutEvaluator,
    )

    n = min(N_ATTACKS, len(real), len(control))
    cols = list(real.columns)
    out: dict[str, Any] = {"rows": {"real": len(real), "synthetic": len(synth)}}
    if "singling-out" in attacks:

        def singling() -> Any:
            e = SinglingOutEvaluator(
                ori=real,
                syn=synth,
                control=control,
                n_attacks=n,
                n_cols=min(3, len(cols)),
                seed=SEED,
                max_attempts=ATTEMPTS_PER_ATTACK * n,
            )
            e.evaluate(mode="multivariate")
            return e.results()

        res, notes = _collect(singling)
        out["singling_out"] = _outcome(res, n, notes)
    if "linkability" in attacks:
        if len(cols) < 2:
            out["linkability"] = {"skipped": "needs at least 2 columns"}
        else:
            half = len(cols) // 2

            def linkability() -> Any:
                e = LinkabilityEvaluator(
                    ori=real,
                    syn=synth,
                    control=control,
                    n_attacks=n,
                    aux_cols=(cols[:half], cols[half:]),
                    n_neighbors=min(MAX_NEIGHBORS, len(synth)),
                )
                e.evaluate(n_jobs=1)
                return e.results()

            res, notes = _collect(linkability)
            out["linkability"] = _outcome(res, n, notes)
    if "inference" in attacks:
        if len(cols) < 2:
            out["inference"] = {"skipped": "needs at least 2 columns"}
        else:
            per_secret = {}
            for secret in cols:

                def inference(secret: str = secret) -> Any:
                    e = InferenceEvaluator(
                        ori=real,
                        syn=synth,
                        control=control,
                        aux_cols=[c for c in cols if c != secret],
                        secret=secret,
                        regression=real[secret].dtype.kind == "f",
                        n_attacks=n,
                    )
                    e.evaluate(n_jobs=1)
                    return e.results()

                res, notes = _collect(inference)
                per_secret[secret] = _outcome(res, n, notes)
            out["inference"] = {"secrets": per_secret}
    return out


def run(
    real: dict[str, pa.Table],
    synth: dict[str, pa.Table],
    control: dict[str, pa.Table],
    attacks: list[str],
) -> tuple[str, dict[str, Any]]:
    mod = require("anonymeter", "anonymeter", name="Anonymeter")
    from importlib import metadata

    try:
        tool_version = metadata.version("anonymeter")
    except metadata.PackageNotFoundError:
        tool_version = str(getattr(mod, "__version__", "unknown"))
    tables: dict[str, Any] = {}
    skipped: dict[str, list[str]] = {}
    for name in sorted(real):
        keep = [f.name for f in real[name].schema if _supported(f.type)]
        dropped = [c for c in real[name].column_names if c not in keep]
        if dropped:
            skipped[name] = dropped
        if not keep:
            raise EvaluateInputError(f"table {name!r} has no column Anonymeter can evaluate")
        frames = [t[name].select(keep).to_pandas() for t in (real, synth, control)]
        if min(len(frames[0]), len(frames[2])) < MIN_ROWS or len(frames[1]) < 1:
            raise EvaluateInputError(
                f"table {name!r}: Anonymeter needs at least {MIN_ROWS} real and control rows "
                "and one synthetic row"
            )
        try:
            tables[name] = _table_results(frames[0], frames[1], frames[2], attacks)
        except (ValueError, KeyError, TypeError) as exc:
            raise EvaluateInputError(
                f"Anonymeter could not evaluate table {name!r}: {exc}"
            ) from None
    return tool_version, {
        "attacks": list(attacks),
        "tables": tables,
        "skipped_columns": skipped,
    }


def summary(results: dict[str, Any]) -> list[str]:
    lines = ["Anonymeter privacy risk (0 is best, 1 is worst)"]
    for table, res in results["tables"].items():
        lines.append(f"  {table}")
        for key in ("singling_out", "linkability"):
            if key in res:
                lines.append(f"    {key}: {_risk(res[key])}")
        if "inference" in res:
            for secret, r in res["inference"].get("secrets", {}).items():
                lines.append(f"    inference of {secret}: {_risk(r)}")
    return lines


def _risk(res: dict[str, Any]) -> str:
    if "skipped" in res:
        return f"skipped ({res['skipped']})"
    lo, hi = res["risk"]["ci"]
    return f"{res['risk']['value']:.3f} (95% CI {lo:.3f} to {hi:.3f})"
