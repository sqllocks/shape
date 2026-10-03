"""The local synthetic-data report card (W3-05): whether a synthetic dataset is fit to use, in one
document.

``report_card(real, synthetic, ...)`` runs what Shape already has and states, per section, what
passed, what failed and what was not run. It adds no second implementation: the numbers are those
of ``shape fidelity`` (the base scores and the two tiers) and ``shape verify --source`` (the
utility and memorization gates); the only new computation is the membership-inference test.

* **fidelity**: ``compare_tables`` (overall, per table, per column) with its pass marks, and the
  tier 1 (adversarial test) and tier 2 (formats, cardinality, string similarity) summaries.
* **utility**: the utility gate, when the verify configuration has a ``utility`` section and
  scikit-learn is installed.
* **privacy**: the memorization gate, and the membership-inference test when real rows that the
  generator did not see (the holdout) are given.

Membership inference. For up to ``max_rows`` rows of the real data (members) and of the holdout
(non-members) it takes the distance to the closest synthetic row, in the standardised distance of
the memorization gate (numeric columns shared by all three tables, each divided by the real data's
standard deviation, nulls taken as the median). The AUC of telling members from non-members by that
distance is 0.5 when it carries no signal and 1.0 when every member is closer than every
non-member. The test fails when the AUC exceeds ``privacy.max_membership_auc`` (default 0.6).

No value of the data is written to the card: it holds scores, counts, distances, table and column
names, and the indices of reproduced rows.
"""

from __future__ import annotations

import copy
import json
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]

from shape.report import html as _html

from .gates import ValidationContext
from .memorization import (
    DEFAULT_MAX_ROWS,
    SOURCE_ROW_CAP,
    MemorizationGate,
    _numeric_matrix,
    _stride,
    nearest_distances,
)
from .utility import UtilityGate
from .verify import load_tables
from .verifyconfig import VerifyConfig, load_verify_config

FORMAT = "shape-report-card"
VERSION = 1
SECTIONS = ("fidelity", "utility", "privacy")
TIERS = (1, 2)
#: The membership test's own threshold: an AUC above it fails (0.5 is no signal).
DEFAULT_MAX_MEMBERSHIP_AUC = 0.6
#: The adversarial AUC and tier 2 pass rate of ``shape fidelity --tier`` (its defaults).
TIER1_MAX_AUC = 0.75
TIER2_MIN_PASS_RATE = 1.0
MIN_MEMBERSHIP_ROWS = 10
INSTALL_ADVANCED = 'pip install "sqllocks-shape[advanced]"'

PASS, FAIL, NOT_RUN = "pass", "fail", "not_run"

Array = npt.NDArray[np.float64]


class ReportCardError(ValueError):
    """The inputs of a report card are unusable (exit code 2)."""


# --- helpers ---------------------------------------------------------------------------------


def _clean(value: Any) -> Any:
    """``value`` as plain JSON data: numpy scalars as Python ones, NaN and infinity as ``None``."""
    if isinstance(value, Mapping):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _gate(
    name: str,
    status: str,
    *,
    table: str | None = None,
    value: float | None = None,
    threshold: float | None = None,
    relation: str | None = None,
    messages: Sequence[str] = (),
    reason: str | None = None,
) -> dict[str, Any]:
    out: dict[str, Any] = {"name": name, "status": status}
    if table is not None:
        out["table"] = table
    if value is not None:
        out["value"] = value
    if threshold is not None:
        out["threshold"] = threshold
        out["relation"] = relation
    if messages:
        out["messages"] = list(messages)
    if reason is not None:
        out["reason"] = reason
    return _clean(out)  # type: ignore[no-any-return]


def _section(
    gates: list[dict[str, Any]],
    metrics: dict[str, Any],
    notes: Sequence[str] = (),
    reason: str | None = None,
) -> dict[str, Any]:
    statuses = {g["status"] for g in gates}
    status = FAIL if FAIL in statuses else PASS if PASS in statuses else NOT_RUN
    out: dict[str, Any] = {
        "status": status,
        "metrics": _clean(metrics),
        "gates": gates,
        "notes": list(notes),
    }
    if status == NOT_RUN:
        out["reason"] = reason or "; ".join(str(g.get("reason", "")) for g in gates) or "not run"
    return out


def _not_run(name: str, reason: str) -> dict[str, Any]:
    return _section([_gate(name, NOT_RUN, reason=reason)], {}, reason=reason)


def _has_sklearn() -> bool:
    try:
        import sklearn  # noqa: F401
    except ImportError:
        return False
    return True


def _tables(source: Any, label: str) -> dict[str, pa.Table]:
    if isinstance(source, (str, os.PathLike)):
        tables = load_tables(Path(source))
        if not tables:
            raise ReportCardError(f"no data files found in the {label} data: {source}")
        return tables
    if isinstance(source, pa.Table):
        return {"table": source}
    if isinstance(source, Mapping) and all(isinstance(t, pa.Table) for t in source.values()):
        if not source:
            raise ReportCardError(f"the {label} data has no tables")
        return dict(source)
    raise ReportCardError(f"the {label} data must be a path or a mapping of Arrow tables")


def _matched(real: Mapping[str, pa.Table], other: dict[str, pa.Table]) -> dict[str, pa.Table]:
    """Two single tables are compared whatever they are called, as ``shape fidelity`` does."""
    if len(real) == 1 and len(other) == 1:
        return {next(iter(real)): next(iter(other.values()))}
    return other


def _config(config: Any) -> VerifyConfig:
    if config is None:
        return VerifyConfig()
    if isinstance(config, VerifyConfig):
        return config
    if isinstance(config, Mapping):
        return VerifyConfig.from_dict(config)
    return load_verify_config(config)


def _tier_numbers(tiers: Sequence[int]) -> tuple[int, ...]:
    for t in tiers:
        if isinstance(t, bool) or t not in TIERS:
            raise ReportCardError(
                f"tier {t!r} is not part of the report card (use 1 and/or 2; tier 3 is "
                "experimental and has its own `shape fidelity --tier 3`)"
            )
    return tuple(sorted(set(tiers)))


def _sections_named(names: Sequence[str]) -> tuple[str, ...]:
    for n in names:
        if n not in SECTIONS:
            raise ReportCardError(f"unknown section {n!r} in --require (use {', '.join(SECTIONS)})")
    return tuple(dict.fromkeys(names))


# --- fidelity --------------------------------------------------------------------------------


def _fidelity(
    real: dict[str, pa.Table],
    synth: dict[str, pa.Table],
    common: list[str],
    tiers: tuple[int, ...],
) -> dict[str, Any]:
    from shape.generation.report import compare_tables

    report = compare_tables(real, synth)
    d = report.to_dict()
    marks = report.thresholds
    gates = [
        _gate(
            "overall_score",
            PASS if report.overall_score >= marks.min_overall else FAIL,
            value=d["overall_score"],
            threshold=marks.min_overall,
            relation=">=",
        )
    ]
    problems = list(report.issues)
    for name, tf in report.tables.items():
        if tf.present:
            gates.append(
                _gate(
                    "table_score",
                    FAIL if tf.score < marks.min_table else PASS,
                    table=name,
                    value=d["tables"][name]["score"],
                    threshold=marks.min_table,
                    relation=">=",
                )
            )
        else:
            problems.append(f"table {name}: missing from the synthetic data")
        problems += [f"table {name}: {x}" for x in tf.issues]
        problems += [
            f"table {name}: column {c} is missing from the synthetic data"
            for c in tf.missing_columns
        ]
    gates.append(_gate("coverage", FAIL if problems else PASS, messages=problems))
    metrics: dict[str, Any] = {
        k: d[k] for k in ("overall_score", "thresholds", "missing_tables", "extra_tables", "issues")
    }
    metrics["tables"] = d["tables"]
    if 1 in tiers:
        metrics["tier1"] = _tier1(real, synth, common, gates)
    if 2 in tiers:
        metrics["tier2"] = _tier2(real, synth, common, gates)
    return _section(gates, metrics)


def _tier1(
    real: dict[str, pa.Table],
    synth: dict[str, pa.Table],
    common: list[str],
    gates: list[dict[str, Any]],
) -> dict[str, Any]:
    from shape.fidelity.tier1 import Tier1Profiler

    profiler = Tier1Profiler(adversarial_threshold=TIER1_MAX_AUC)
    out: dict[str, Any] = {}
    for name in common:
        prof = profiler.profile_pair(real[name], synth[name], name)
        full = prof.to_dict()
        adv = full["adversarial"]
        out[name] = {
            "adversarial": adv,
            "mixture_fit_columns": sorted(prof.gmm_fits),
            "conditional_profiles": len(prof.conditional_profiles),
            "temporal_columns": sorted(prof.temporal_profiles),
            "periodic_columns": sorted(c for c, p in prof.periodicity.items() if p.is_periodic),
        }
        if adv is None:
            why = next((n for n in prof.notes if n.startswith("adversarial test")), None)
            reason = why or "the adversarial test did not run"
            gates.append(_gate("tier1_adversarial_auc", NOT_RUN, table=name, reason=reason))
            out[name]["adversarial_reason"] = reason
        else:
            gates.append(
                _gate(
                    "tier1_adversarial_auc",
                    PASS if prof.adversarial and prof.adversarial.passed else FAIL,
                    table=name,
                    value=adv["auc_roc"],
                    threshold=TIER1_MAX_AUC,
                    relation="<",
                )
            )
    return out


def _tier2(
    real: dict[str, pa.Table],
    synth: dict[str, pa.Table],
    common: list[str],
    gates: list[dict[str, Any]],
) -> dict[str, Any]:
    from shape.fidelity.tier2 import run_tier2

    out: dict[str, Any] = {}
    for name in common:
        report = run_tier2(real[name], synth[name], None)
        out[name] = report.to_dict()
        rate = report.passing_rate()
        gates.append(
            _gate(
                "tier2_pass_rate",
                PASS if rate >= TIER2_MIN_PASS_RATE else FAIL,
                table=name,
                value=rate,
                threshold=TIER2_MIN_PASS_RATE,
                relation=">=",
            )
        )
    return out


# --- utility ---------------------------------------------------------------------------------


def _utility(
    real: dict[str, pa.Table],
    synth: dict[str, pa.Table],
    rules: dict[str, Any],
    configured: bool,
    required: bool,
) -> dict[str, Any]:
    if "utility" not in rules:
        why = (
            "the verify configuration has no `utility` section"
            if configured
            else "no verify configuration was given (--config), so there is no `utility` section"
        )
        return _not_run("utility_retention", why)
    if not _has_sklearn():
        message = f"the utility gate needs scikit-learn: {INSTALL_ADVANCED}"
        if required:
            raise ReportCardError(message)
        return _not_run("utility_retention", message)
    result = UtilityGate().check(
        ValidationContext(tables=synth, source_tables=real, config=dict(rules))
    )
    details = _clean(result.details)
    gate = _gate(
        "utility_retention",
        PASS if result.passed else FAIL,
        value=details.get("retention"),
        threshold=details.get("min_retention"),
        relation=">=",
        messages=result.errors,
    )
    return _section([gate], details, notes=result.warnings)


# --- privacy ---------------------------------------------------------------------------------


def membership_auc(member_distances: Array, non_member_distances: Array) -> float:
    """The AUC of telling members from non-members by the distance to the closest synthetic row:
    the chance that a random member is closer than a random non-member (a tie counts half).
    0.5 means the distance carries no signal; 1.0 means every member is closer."""
    m, n = len(member_distances), len(non_member_distances)
    if m == 0 or n == 0:
        raise ValueError("membership_auc needs at least one member and one non-member")
    both = np.concatenate([member_distances, non_member_distances])
    _, inverse, counts = np.unique(both, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(counts) - (counts - 1) / 2.0)[inverse]  # average ranks, ties shared
    u = float(ranks[m:].sum()) - n * (n + 1) / 2.0  # pairs where the non-member is farther
    return u / (m * n)


def _summary(d: Array) -> dict[str, float]:
    return {"p05": float(np.percentile(d, 5)), "median": float(np.median(d))}


def _pick(n: int, cap: int, rng: np.random.Generator) -> npt.NDArray[np.int64]:
    if n <= cap:
        return np.arange(n, dtype=np.int64)
    return np.sort(rng.choice(n, size=cap, replace=False)).astype(np.int64)


def _membership_table(
    real: pa.Table, synth: pa.Table, hold: pa.Table, max_rows: int, rng: np.random.Generator
) -> dict[str, Any]:
    shared = [c for c in real.column_names if c in synth.column_names and c in hold.column_names]
    s_mat, r_mat, used = _numeric_matrix(synth, real, shared)
    h_mat, _, used_h = _numeric_matrix(hold, real, shared)
    if used != used_h:  # a column the holdout cannot standardise the same way drops out
        shared = [c for c in used if c in used_h]
        s_mat, r_mat, used = _numeric_matrix(synth, real, shared)
        h_mat, _, used_h = _numeric_matrix(hold, real, shared)
    if not used:
        return {
            "status": NOT_RUN,
            "reason": "the real, synthetic and holdout tables share no numeric column with "
            "any variation to measure a distance on",
        }
    if len(s_mat) == 0:
        return {"status": NOT_RUN, "reason": "the synthetic table has no rows"}
    if min(len(r_mat), len(h_mat)) < MIN_MEMBERSHIP_ROWS:
        return {
            "status": NOT_RUN,
            "reason": f"fewer than {MIN_MEMBERSHIP_ROWS} rows on one side "
            f"({len(r_mat)} real rows, {len(h_mat)} holdout rows)",
        }
    members = r_mat[_pick(len(r_mat), max_rows, rng)]
    non_members = h_mat[_pick(len(h_mat), max_rows, rng)]
    target = s_mat[_stride(len(s_mat), SOURCE_ROW_CAP)]
    dm, dn = nearest_distances(members, target), nearest_distances(non_members, target)
    return {
        "auc": membership_auc(dm, dn),
        "members": int(len(dm)),
        "non_members": int(len(dn)),
        "synthetic_rows": int(len(target)),
        "columns": used,
        "member_distance": _summary(dm),
        "non_member_distance": _summary(dn),
    }


def _membership(
    real: dict[str, pa.Table],
    synth: dict[str, pa.Table],
    holdout: dict[str, pa.Table] | None,
    common: list[str],
    rules: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    opts = dict(rules.get("privacy") or {})
    max_auc = float(opts.get("max_membership_auc", DEFAULT_MAX_MEMBERSHIP_AUC))
    seed = int(opts.get("seed", 0))
    max_rows = int((rules.get("memorization") or {}).get("max_rows", DEFAULT_MAX_ROWS))
    if holdout is None:
        reason = (
            "no holdout was given (--holdout): membership inference needs real rows that were "
            "not given to the generator"
        )
        return (
            {"status": NOT_RUN, "reason": reason},
            [_gate("membership_inference", NOT_RUN, reason=reason)],
        )
    rng = np.random.default_rng(seed)
    tables: dict[str, Any] = {}
    gates: list[dict[str, Any]] = []
    for name in common:
        hold = holdout.get(name)
        if hold is None:
            entry: dict[str, Any] = {
                "status": NOT_RUN,
                "reason": "the holdout has no table of this name",
            }
        else:
            entry = _membership_table(real[name], synth[name], hold, max_rows, rng)
            if "auc" in entry:
                entry["status"] = PASS if entry["auc"] <= max_auc else FAIL
        tables[name] = entry
        gates.append(
            _gate(
                "membership_inference",
                entry["status"],
                table=name,
                value=entry.get("auc"),
                threshold=max_auc if "auc" in entry else None,
                relation="<=",
                reason=entry.get("reason"),
            )
        )
    statuses = {e["status"] for e in tables.values()}
    status = FAIL if FAIL in statuses else PASS if PASS in statuses else NOT_RUN
    metrics: dict[str, Any] = {
        "status": status,
        "max_membership_auc": max_auc,
        "seed": seed,
        "max_rows": max_rows,
        "tables": tables,
    }
    if status == NOT_RUN:
        metrics["reason"] = "; ".join(f"{n}: {e['reason']}" for n, e in tables.items())
    return metrics, gates


def _privacy(
    real: dict[str, pa.Table],
    synth: dict[str, pa.Table],
    holdout: dict[str, pa.Table] | None,
    common: list[str],
    rules: dict[str, Any],
) -> dict[str, Any]:
    ctx = ValidationContext(
        tables={n: synth[n] for n in common},
        source_tables={n: real[n] for n in common},
        config=dict(rules),
    )
    res = MemorizationGate().check(ctx)
    compared = res.details["tables"]
    if compared:
        rate = max(t["exact_match_rate"] for t in compared.values())
        mem = _gate(
            "memorization",
            PASS if res.passed else FAIL,
            value=rate,
            messages=res.errors,
        )
    else:
        mem = _gate(
            "memorization",
            NOT_RUN,
            reason="no table had rows and columns in common with the real data",
        )
    metrics, gates = _membership(real, synth, holdout, common, rules)
    return _section(
        [mem, *gates],
        {"memorization": res.details, "membership_inference": metrics},
        notes=res.warnings,
    )


# --- the card --------------------------------------------------------------------------------


def _input(tables: Mapping[str, pa.Table]) -> dict[str, Any]:
    from shape.repro import dataset_id

    return {
        "dataset_id": dataset_id(tables),
        "tables": {n: t.num_rows for n, t in sorted(tables.items())},
    }


def _manifest_input(manifest: Any, synthetic_id: str) -> dict[str, Any] | None:
    if manifest is None:
        return None
    from shape.scenario.manifest import ManifestBuilder, RunManifest

    m = manifest if isinstance(manifest, RunManifest) else ManifestBuilder.from_file(manifest)
    recorded = m.dataset_id
    return {
        "run_id": m.run_id,
        "reproducibility": _clean(m.reproducibility),
        "dataset_id": recorded,
        "dataset_id_matches": (recorded == synthetic_id) if recorded else None,
    }


def _verdict(sections: dict[str, Any], require: tuple[str, ...]) -> list[str]:
    reasons: list[str] = []
    for name, s in sections.items():
        if s["status"] == FAIL:
            bad = [
                g["name"] + (f" ({g['table']})" if g.get("table") else "")
                for g in s["gates"]
                if g["status"] == FAIL
            ]
            reasons.append(f"{name}: failed ({', '.join(bad)})")
    for name in require:
        s = sections[name]
        if s["status"] == NOT_RUN:
            reasons.append(f"{name}: required but not run ({s['reason']})")
            continue
        for g in s["gates"]:
            if g["status"] == NOT_RUN:
                where = f" ({g['table']})" if g.get("table") else ""
                reasons.append(
                    f"{name}: {g['name']}{where} required but not run ({g.get('reason', '')})"
                )
    return reasons


class ReportCard:
    """A report card: ``to_dict()``, ``to_markdown()`` and ``to_html()``."""

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = parse_report_card(data)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ReportCard:
        return cls(data)

    @property
    def overall(self) -> str:
        return str(self._data["overall"])

    @property
    def exit_code(self) -> int:
        """0 when every section that ran passed (and every required one ran), 1 when not."""
        return 0 if self.overall == PASS else 1

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self._data)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self._data, indent=indent, allow_nan=False)

    def to_markdown(self) -> str:
        return render_markdown(self._data)

    def to_html(self) -> str:
        return render_html(self._data)


def report_card(
    real: Any,
    synthetic: Any,
    config: Any = None,
    tiers: Sequence[int] = (1, 2),
    holdout: Any = None,
    manifest: Any = None,
    *,
    require: Sequence[str] = (),
) -> ReportCard:
    """The report card of ``synthetic`` against ``real``.

    ``real``, ``synthetic`` and ``holdout`` are a path (a file, or a directory of one file per
    table, read as ``shape fidelity`` reads them), one Arrow table, or a mapping of table name to
    Arrow table. ``config`` is a verify configuration (a path, a mapping, a ``VerifyConfig``);
    ``tiers`` are the fidelity tiers to run (1 and/or 2); ``holdout`` is real rows that were not
    given to the generator, for the membership-inference test; ``manifest`` is the run manifest of
    the generation (a path). ``require`` names sections that must have run: a required section (or
    one of its tests) that was not run fails the card, and a missing scikit-learn then raises
    :class:`ReportCardError` for ``utility``.

    Raises :class:`ReportCardError` (a ``ValueError``) for input that cannot be used, such as no
    table in common, and ``FileNotFoundError`` for a path that does not exist."""
    from shape import __version__

    tier_set = _tier_numbers(tiers)
    required = _sections_named(require)
    cfg = _config(config)
    rules = dict(cfg.rules)
    real_t = _tables(real, "real")
    synth_loaded = _tables(synthetic, "synthetic")
    hold_loaded = _tables(holdout, "holdout") if holdout is not None else None
    synth_t = _matched(real_t, synth_loaded)
    hold_t = _matched(real_t, hold_loaded) if hold_loaded is not None else None
    common = [n for n in real_t if n in synth_t]
    if not common:
        raise ReportCardError(
            "the real and the synthetic data have no table in common "
            f"(real: {', '.join(real_t)}; synthetic: {', '.join(synth_t)})"
        )
    inputs: dict[str, Any] = {
        "real": _input(real_t),
        "synthetic": _input(synth_loaded),
        "holdout": _input(hold_loaded) if hold_loaded is not None else None,
    }
    inputs["manifest"] = _manifest_input(manifest, inputs["synthetic"]["dataset_id"])
    sections = {
        "fidelity": _fidelity(real_t, synth_t, common, tier_set),
        "utility": _utility(real_t, synth_t, rules, config is not None, "utility" in required),
        "privacy": _privacy(real_t, synth_t, hold_t, common, rules),
    }
    reasons = _verdict(sections, required)
    return ReportCard(
        {
            "format": FORMAT,
            "version": VERSION,
            "shape_version": __version__,
            "inputs": inputs,
            "sections": sections,
            "require": list(required),
            "overall": FAIL if reasons else PASS,
            "overall_reasons": reasons,
        }
    )


def parse_report_card(doc: Any) -> dict[str, Any]:
    """``doc`` if it is a report card this Shape reads; :class:`ReportCardError` if not."""
    if not isinstance(doc, Mapping) or doc.get("format") != FORMAT:
        raise ReportCardError(f"not a report card: expected format {FORMAT!r}")
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ReportCardError(f"unsupported report card version {version!r}")
    if version > VERSION:
        raise ReportCardError(
            f"this report card is version {version}, written by a newer Shape; this Shape reads "
            f"versions up to {VERSION}. Upgrade Shape to read it"
        )
    for key in ("inputs", "sections", "overall"):
        if key not in doc:
            raise ReportCardError(f"the report card has no {key!r}")
    return dict(doc)


def load_report_card(path: str | Path) -> dict[str, Any]:
    """The report card in a JSON file, checked (``format``, ``version``)."""
    p = Path(path)
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise ReportCardError(f"{p} is not valid JSON: {exc}") from exc
    return parse_report_card(doc)


# --- renderers -------------------------------------------------------------------------------

Table = tuple[str, list[str], list[list[Any]]]


def _label(status: str) -> str:
    return status.replace("_", " ").upper()


def _get(d: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(d, Mapping):
            return None
        d = d.get(key)
    return d


def _gates_table(section: Mapping[str, Any]) -> Table:
    rows = []
    for g in section.get("gates") or []:
        threshold = g.get("threshold")
        rows.append(
            [
                g.get("name"),
                g.get("table"),
                g.get("status"),
                g.get("value"),
                None if threshold is None else f"{g.get('relation', '')} {threshold:g}",
                g.get("reason") or "; ".join(g.get("messages") or []) or None,
            ]
        )
    return ("Gates", ["gate", "table", "status", "value", "threshold", "detail"], rows)


def _fidelity_tables(m: Mapping[str, Any]) -> list[Table]:
    tables = m.get("tables") or {}
    scores = [
        [n, t.get("row_count_real"), t.get("row_count_synth"), t.get("score")]
        for n, t in tables.items()
    ]
    scores.append(["(overall)", None, None, m.get("overall_score")])
    columns = [
        [
            n,
            c,
            v.get("score"),
            v.get("dtype_match"),
            v.get("null_rate_delta"),
            v.get("ks_statistic"),
            v.get("value_overlap"),
        ]
        for n, t in tables.items()
        for c, v in (t.get("columns") or {}).items()
    ]
    out: list[Table] = [
        ("Scores (0-100)", ["table", "real rows", "synthetic rows", "score"], scores),
        (
            "Columns",
            [
                "table",
                "column",
                "score",
                "type matches",
                "null-rate difference",
                "KS statistic",
                "value overlap",
            ],
            columns,
        ),
    ]
    if "tier1" in m:
        out.append(
            (
                "Tier 1: adversarial test",
                ["table", "AUC", "accuracy", "rows", "result"],
                [
                    [
                        n,
                        _get(t, "adversarial", "auc_roc"),
                        _get(t, "adversarial", "accuracy"),
                        _get(t, "adversarial", "n_samples"),
                        (
                            _label(PASS if _get(t, "adversarial", "passed") else FAIL)
                            if t.get("adversarial")
                            else "not run: " + str(t.get("adversarial_reason"))
                        ),
                    ]
                    for n, t in m["tier1"].items()
                ],
            )
        )
    if "tier2" in m:
        rows = []
        for n, t in m["tier2"].items():
            fmt = list((t.get("format_preservation") or {}).values())
            card = list((t.get("cardinality") or {}).values())
            rows.append(
                [
                    n,
                    t.get("passing_rate"),
                    f"{sum(1 for x in fmt if x.get('passed'))}/{len(fmt)}",
                    f"{sum(1 for x in card if x.get('passed'))}/{len(card)}",
                ]
            )
        out.append(
            (
                "Tier 2: formats and cardinality",
                ["table", "passing rate", "format checks passed", "cardinality checks passed"],
                rows,
            )
        )
    return out


def _utility_tables(m: Mapping[str, Any]) -> list[Table]:
    if not m:
        return []
    keys = (
        "table",
        "target",
        "task",
        "metric",
        "real_score",
        "synthetic_score",
        "retention",
        "min_retention",
        "train_rows_real",
        "train_rows_synthetic",
        "test_rows",
    )
    return [
        (
            "Utility (train on synthetic, test on real)",
            ["measure", "value"],
            [[k, m.get(k)] for k in keys if k in m],
        )
    ]


def _privacy_tables(m: Mapping[str, Any]) -> list[Table]:
    out: list[Table] = []
    mem = _get(m, "memorization", "tables") or {}
    if mem:
        out.append(
            (
                "Memorization",
                [
                    "table",
                    "synthetic rows",
                    "exact-match rate",
                    "reproduced rows",
                    "closest-row distance min",
                    "p05",
                    "median",
                ],
                [
                    [
                        n,
                        t.get("rows"),
                        t.get("exact_match_rate"),
                        t.get("reproduced_rows"),
                        _get(t, "nn_distance", "min"),
                        _get(t, "nn_distance", "p05"),
                        _get(t, "nn_distance", "median"),
                    ]
                    for n, t in mem.items()
                ],
            )
        )
    mi = _get(m, "membership_inference", "tables") or {}
    if mi:
        out.append(
            (
                "Membership inference",
                [
                    "table",
                    "result",
                    "AUC",
                    "members",
                    "non-members",
                    "member p05",
                    "member median",
                    "non-member p05",
                    "non-member median",
                ],
                [
                    [
                        n,
                        t.get("status"),
                        t.get("auc"),
                        t.get("members"),
                        t.get("non_members"),
                        _get(t, "member_distance", "p05"),
                        _get(t, "member_distance", "median"),
                        _get(t, "non_member_distance", "p05"),
                        _get(t, "non_member_distance", "median"),
                    ]
                    for n, t in mi.items()
                ],
            )
        )
    return out


def _section_tables(name: str, section: Mapping[str, Any]) -> list[Table]:
    metrics = section.get("metrics") or {}
    if name == "fidelity":
        body = _fidelity_tables(metrics) if metrics else []
    elif name == "utility":
        body = _utility_tables(metrics)
    elif name == "privacy":
        body = _privacy_tables(metrics)
    else:
        body = []
    return [*body, _gates_table(section)]


def _input_rows(card: Mapping[str, Any]) -> list[list[Any]]:
    rows = []
    for key in ("real", "synthetic", "holdout"):
        i = _get(card, "inputs", key)
        if i:
            tables = ", ".join(f"{n} ({r:,} rows)" for n, r in (i.get("tables") or {}).items())
            rows.append([key, tables, i.get("dataset_id")])
    return rows


def _manifest_lines(card: Mapping[str, Any]) -> list[str]:
    m = _get(card, "inputs", "manifest")
    if not m:
        return []
    tup = ", ".join(f"{k}={v}" for k, v in (m.get("reproducibility") or {}).items())
    lines = [f"Run {m.get('run_id') or '(no id)'}: {tup or 'no reproducibility tuple recorded'}"]
    matches = m.get("dataset_id_matches")
    if matches is None:
        lines.append("The manifest records no dataset id to compare with.")
    elif matches:
        lines.append(f"The recorded dataset id equals the synthetic input's: {m.get('dataset_id')}")
    else:
        lines.append(
            f"DIFFERENT: the manifest records dataset id {m.get('dataset_id')}, which is not the "
            "synthetic input's"
        )
    return lines


def _text(value: Any) -> str:
    if value is None:
        return "–"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _md(value: Any) -> str:
    return (
        _text(value)
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("|", "\\|")
        .replace("\n", " ")
    )


def render_markdown(card: Mapping[str, Any]) -> str:
    """The card as Markdown (from the card's JSON; nothing else is read)."""
    overall = str(card.get("overall"))
    lines = [
        "# Report card",
        "",
        f"**Overall: {_label(overall)}** · Shape {_md(card.get('shape_version'))} · "
        f"format {_md(card.get('format'))} version {_md(card.get('version'))}",
        "",
    ]
    for r in card.get("overall_reasons") or []:
        lines.append(f"- {_md(r)}")
    if card.get("overall_reasons"):
        lines.append("")
    lines += ["## Inputs", "", "| input | tables | dataset id |", "|---|---|---|"]
    lines += ["| " + " | ".join(_md(c) for c in row) + " |" for row in _input_rows(card)]
    lines.append("")
    lines += [_md(x) + "  " for x in _manifest_lines(card)]
    for name, section in (card.get("sections") or {}).items():
        status = str(section.get("status"))
        lines += ["", f"## {name.capitalize()}: {_label(status)}", ""]
        if status == NOT_RUN:
            lines += [f"Not run: {_md(section.get('reason'))}", ""]
        for note in section.get("notes") or []:
            lines.append(f"- {_md(note)}")
        for caption, headers, rows in _section_tables(name, section):
            if not rows:
                continue
            lines += [
                "",
                f"### {caption}",
                "",
                "| " + " | ".join(headers) + " |",
                "|" + "---|" * len(headers),
            ]
            lines += ["| " + " | ".join(_md(c) for c in row) + " |" for row in rows]
    lines += [
        "",
        "A passing card is evidence, not a guarantee: see docs/REPORT_CARD.md for what each "
        "number means and where it stops.",
        "",
    ]
    return "\n".join(lines)


def _table_html(headers: list[str], rows: list[list[Any]]) -> str:
    e = _html.escape
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join(
        "<tr>"
        + "".join(
            f'<td class="num">{_html.format_value(c)}</td>'
            if isinstance(c, (int, float)) and not isinstance(c, bool)
            else f"<td>{e(_text(c))}</td>"
            for c in row
        )
        + "</tr>"
        for row in rows
    )
    return (
        f'<div class="tablewrap"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def _badge(status: str) -> str:
    return f'<span class="status {_html.escape(status)}">{_html.escape(_label(status))}</span>'


def render_html(card: Mapping[str, Any]) -> str:
    """The card as one self-contained HTML page (inline styles, no script, no request)."""
    e = _html.escape
    overall = str(card.get("overall"))
    parts = [
        f"<h1>Report card {_badge(overall)}</h1>",
        f'<p class="meta">Shape {e(card.get("shape_version"))} · format '
        f"{e(card.get('format'))} version {e(card.get('version'))}</p>",
    ]
    if card.get("overall_reasons"):
        parts.append(
            "<ul>" + "".join(f"<li>{e(r)}</li>" for r in card["overall_reasons"]) + "</ul>"
        )
    parts += [
        "<h2>Inputs</h2>",
        _table_html(["input", "tables", "dataset id"], _input_rows(card)),
    ]
    parts += [f'<p class="meta">{e(x)}</p>' for x in _manifest_lines(card)]
    for name, section in (card.get("sections") or {}).items():
        status = str(section.get("status"))
        parts.append(f"<h2>{e(name.capitalize())} {_badge(status)}</h2>")
        if status == NOT_RUN:
            parts.append(f'<p class="meta">Not run: {e(section.get("reason"))}</p>')
        if section.get("notes"):
            parts.append(
                '<ul class="warn">'
                + "".join(f"<li>{e(n)}</li>" for n in section["notes"])
                + "</ul>"
            )
        for caption, headers, rows in _section_tables(name, section):
            if rows:
                parts += [f"<h3>{e(caption)}</h3>", _table_html(headers, rows)]
    parts.append(
        '<p class="meta">A passing card is evidence, not a guarantee: see docs/REPORT_CARD.md '
        "for what each number means and where it stops.</p>"
    )
    return _html.document("Shape report card", "".join(parts))
