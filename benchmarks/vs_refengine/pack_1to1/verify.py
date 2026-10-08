"""Scenario-pack parity (P6-14): Shape's pack code against the pinned baseline's, on the five
reference inputs in ``tests/fixtures/packs/``. Runs in the *baseline* venv (needs pandas, scipy and
the baseline importable); Shape is driven in its own venv through ``shape_worker.py`` and the
``shape pack`` command.

    source scripts/env.sh && "$REFENGINE_PY" \\
        benchmarks/vs_refengine/pack_1to1/verify.py [--negative-control]

For each input the harness checks, in this order (equivalence only; nothing here is timed):

1. **Loads**: the parsed structure of the pack (or spec) is equal field for field. Fields only one
   tool has are named in ``pack_common.py`` (``SHAPE_ONLY_FIELDS``, ``BASELINE_ONLY_FIELDS``); a
   Shape name is mapped back to the baseline's first (``NAME_MAP_BACK``, D-13).
2. **Validates**: the same verdict and the same errors. Shape's warnings are the baseline's plus
   the allow-listed extras (none for these inputs).
3. **Runs** at pack scale ``fabric_demo``, seed 42, into ``$BENCH_OUT_DIR/pack_1to1/``: the same
   success, gate results, event count, files written (the manifest's name aside) and, per table,
   rows and columns.
4. **Manifest**: Shape's key set contains every key of the baseline's manifests, both the
   ``$REFENGINE_ROOT/pack_output*/*_manifest.json`` files and the baseline's own run here. The
   values of ``spec_hash``, ``outputs`` and ``tables.*.file_paths`` differ by the named defects
   (``ALLOWED`` PK-1..3); everything else is equal except identity fields (ID-1).
5. **T-21 (a)-(f)** for every input without chaos: the written tables of the baseline at seed 42
   (reference) and 43-46 (its own spread) against Shape at seed 1042, by the clauses and
   tolerances of ``domain_1to1/verify.py`` (imported, not copied): structure, null rates,
   KS, TVD and vocabulary, FK integrity and fan-out. (g) and (h) are not part of P6-14.

``--negative-control`` proves the checks can fail: it tampers with Shape's output (a numeric
column scaled, a categorical column overwritten, foreign keys broken) and with its manifest (a key
dropped), runs the same checks, and exits 0 only if each was flagged. Exit codes of a normal run:
0 every check holds, 1 a check failed, 2 a run could not be produced.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[0]))
import pack_common as common  # noqa: E402
import pandas as pd  # noqa: E402
import pyarrow.parquet as pq  # noqa: E402
from paths import BENCH_OUT_DIR, REFENGINE_PY, REFENGINE_ROOT, SHAPE_PY  # noqa: E402


def _load_domain_verify() -> Any:
    """``domain_1to1/verify.py`` as a module (its name would clash with this file's)."""
    path = HERE.parent / "domain_1to1" / "verify.py"
    spec = importlib.util.spec_from_file_location("domain_verify", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["domain_verify"] = mod
    spec.loader.exec_module(mod)
    return mod


DV = _load_domain_verify()
OUT = BENCH_OUT_DIR / "pack_1to1"


def run_json(cmd: list[str]) -> dict[str, Any]:
    r = subprocess.run(cmd, capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if line.startswith("RESULT_JSON "):
            return dict(json.loads(line[len("RESULT_JSON ") :]))
    sys.stderr.write(r.stdout[-1500:] + r.stderr[-1500:])
    raise RuntimeError(f"no result from {cmd[1]} (exit {r.returncode})")


def shape_cli(*args: str) -> subprocess.CompletedProcess[str]:
    code = "import sys; from shape.cli.main import main; sys.exit(main(sys.argv[1:]))"
    return subprocess.run([str(SHAPE_PY), "-c", code, *args], capture_output=True, text=True)


# ─────────────────────────────────────────────────────────────────────────────
# producing the runs
# ─────────────────────────────────────────────────────────────────────────────


def baseline_run(name: str, kind: str, seeds: list[int]) -> dict[str, Any]:
    out = OUT / "baseline" / name
    shutil.rmtree(out, ignore_errors=True)
    return run_json(
        [
            str(REFENGINE_PY),
            str(HERE / "baseline_worker.py"),
            "--input",
            str(common.FIXTURES / name),
            "--kind",
            kind,
            "--scale",
            common.SCALE,
            "--seeds",
            ",".join(map(str, seeds)),
            "--out",
            str(out),
        ]
    )


def shape_run(name: str, kind: str, seed: int) -> tuple[dict[str, Any], Path]:
    """``shape pack run`` for the input at ``seed``; returns the CLI's JSON and the output dir."""
    out = OUT / "shape" / name / f"seed{seed}"
    shutil.rmtree(out, ignore_errors=True)
    r = shape_cli(
        "pack",
        "run",
        str(common.FIXTURES / name),
        "--scale",
        common.SCALE,
        "--seed",
        str(seed),
        "-o",
        str(out),
        "--json",
    )
    if not r.stdout.strip():
        sys.stderr.write(r.stderr[-1500:])
        raise RuntimeError(
            f"shape pack run {name} seed {seed} printed nothing (exit {r.returncode})"
        )
    doc = dict(json.loads(r.stdout))
    doc["exit_code"] = r.returncode
    return doc, out


# ─────────────────────────────────────────────────────────────────────────────
# comparisons
# ─────────────────────────────────────────────────────────────────────────────


def back(value: Any) -> Any:
    """Map Shape's neutral names back to the baseline's (D-13), in a nested structure."""
    if isinstance(value, dict):
        return {common.NAME_MAP_BACK.get(k, k): back(v) for k, v in value.items()}
    if isinstance(value, list):
        return [back(v) for v in value]
    if isinstance(value, str):
        return common.NAME_MAP_BACK.get(value, value)
    return value


def unwrap_chaos_config(loaded: dict[str, Any]) -> bool:
    """PK-10: the baseline stores a nested ``chaos.config.config`` mapping as one setting; Shape
    merges it into ``chaos.config``. Rewrite the baseline's structure the way Shape reads it.
    True when it applied."""
    chaos = loaded.get("chaos")
    if not isinstance(chaos, dict) or not isinstance(chaos.get("config"), dict):
        return False
    inner = chaos["config"].get("config")
    if not isinstance(inner, dict):
        return False
    rest = {k: v for k, v in chaos["config"].items() if k != "config"}
    chaos["config"] = {**rest, **inner}
    return True


def compare_loaded(
    base: dict[str, Any], shape: dict[str, Any], problems: list[str], tag: str
) -> None:
    b = common.flatten(base)
    s = common.flatten(back(shape))
    for key in list(s):
        if key in common.SHAPE_ONLY_FIELDS or key.split("[")[0] in common.SHAPE_ONLY_FIELDS:
            del s[key]
        elif key.startswith("extra_keys") or key.startswith("path"):
            del s[key]
    for key in list(b):
        if key in common.BASELINE_ONLY_FIELDS:
            del b[key]
    for key in sorted(set(b) | set(s)):
        if b.get(key, "<missing>") != s.get(key, "<missing>"):
            problems.append(
                f"{tag}: loaded {key}: baseline {b.get(key, '<missing>')!r} "
                f"vs Shape {s.get(key, '<missing>')!r}"
            )


def compare_validation(
    base: dict[str, Any], shape: dict[str, Any], problems: list[str], tag: str
) -> None:
    if base["is_valid"] != shape["is_valid"]:
        problems.append(f"{tag}: valid baseline {base['is_valid']} vs Shape {shape['is_valid']}")
    if base["errors"] != shape["errors"]:
        problems.append(f"{tag}: errors baseline {base['errors']} vs Shape {shape['errors']}")
    extra = [w for w in shape["warnings"] if w not in base["warnings"]]
    missing = [w for w in base["warnings"] if w not in shape["warnings"]]
    if extra or missing:
        problems.append(
            f"{tag}: warnings differ: Shape extra {extra}, "
            f"Shape missing {missing} (PK-8 covers none of these)"
        )


SHAPE_KEYS: set[str] = set()  # every key path of every Shape manifest of this run (literal)


def baseline_manifest_keys(literal: bool) -> set[str]:
    """Every key path in the baseline checkout's own pack manifests."""
    keys: set[str] = set()
    for f in sorted(REFENGINE_ROOT.glob("pack_output*/*_manifest.json")):
        keys |= common.manifest_key_paths(json.loads(f.read_text()), literal=literal)
    return keys


def find_manifest(out: Path) -> dict[str, Any]:
    files = sorted(out.glob("*_manifest.json"))
    if not files:
        raise RuntimeError(f"no manifest in {out}")
    return dict(json.loads(files[-1].read_text()))


def compare_runs(
    name: str,
    base_run: dict[str, Any],
    shape_doc: dict[str, Any],
    shape_manifest: dict[str, Any],
    shape_out: Path,
    spec_file: Path | None,
    problems: list[str],
    observed: set[str],
    drop_manifest_key: str | None = None,
    chaos_on: bool = False,
) -> None:
    tag = f"{name} seed {common.REF_SEED}"
    if base_run["success"] != shape_doc["success"]:
        problems.append(
            f"{tag}: success baseline {base_run['success']} vs Shape {shape_doc['success']}"
        )
    if set(base_run["gates"]) != set(shape_doc["gates"]):
        problems.append(
            f"{tag}: gate names baseline {sorted(base_run['gates'])} "
            f"vs Shape {sorted(shape_doc['gates'])}"
        )
    if not chaos_on and base_run["gates"] != shape_doc["gates"]:
        problems.append(f"{tag}: gates baseline {base_run['gates']} vs Shape {shape_doc['gates']}")
    if base_run["events"] != shape_doc["events"]:
        problems.append(
            f"{tag}: events baseline {base_run['events']} vs Shape {shape_doc['events']}"
        )

    def rel(files: list[str], root: Path | None) -> list[str]:
        out = []
        for f in files:
            p = Path(f)
            if root is not None and p.is_absolute():
                p = p.resolve().relative_to(root.resolve())
            out.append("<manifest>" if p.name.endswith("_manifest.json") else p.as_posix())
        return sorted(out)

    if rel(base_run["files"], None) != rel(shape_doc["files"], shape_out):
        problems.append(
            f"{tag}: files baseline {rel(base_run['files'], None)} "
            f"vs Shape {rel(shape_doc['files'], shape_out)}"
        )

    bm = base_run["manifest"]
    sm = {k: v for k, v in shape_manifest.items() if k != drop_manifest_key}
    # Manifest keys: every structural key of the baseline's manifests (the checkout's and this
    # run's). The gate names under `validation` and the categories under `chaos` are data; they
    # are checked literally over all runs in `main` (SHAPE_KEYS).
    want = baseline_manifest_keys(False) | common.manifest_key_paths(bm)
    have = common.manifest_key_paths(back(sm))
    for key in sorted(want - have):
        problems.append(f"{tag}: Shape manifest lacks the baseline key {key}")
    SHAPE_KEYS.update(common.manifest_key_paths(back(sm), literal=True))
    # Values. With chaos on, Shape changes the tables and the gates on purpose (PK-7).
    scalar = ["pack_id", "domain", "scale", "seed", "workspace_id", "lakehouse_id"]
    for key in scalar if chaos_on else [*scalar, "validation", "chaos"]:
        if bm.get(key) != sm.get(key):
            problems.append(
                f"{tag}: manifest {key} baseline {bm.get(key)!r} vs Shape {sm.get(key)!r}"
            )
    if chaos_on:
        observed.add("PK-7")
    if list(bm["tables"]) != list(sm["tables"]):
        problems.append(
            f"{tag}: manifest table order baseline {list(bm['tables'])} "
            f"vs Shape {list(sm['tables'])}"
        )
    for t, bt in bm["tables"].items():
        st = sm["tables"].get(t, {})
        for key in () if chaos_on else ("rows", "columns"):
            if bt.get(key) != st.get(key):
                problems.append(
                    f"{tag}: manifest tables.{t}.{key} baseline "
                    f"{bt.get(key)} vs Shape {st.get(key)}"
                )
        own = st.get("file_paths", [])
        if not all(Path(p).stem == t for p in own):
            problems.append(f"{tag}: Shape tables.{t}.file_paths lists another table's file: {own}")
        matching = [Path(p).name for p in bt.get("file_paths", []) if Path(p).stem == t]
        if [Path(p).name for p in own] != matching:
            problems.append(
                f"{tag}: tables.{t}.file_paths: Shape's own "
                f"files differ from the baseline's matching ones"
            )
        if len(bt.get("file_paths", [])) != len(matching):
            observed.add("PK-1")
    if bm.get("spec_hash", "") != sm.get("spec_hash", ""):
        if (
            spec_file is not None
            and sm.get("spec_hash") == common.sha256_file(spec_file)
            and bm.get("spec_hash") == ""
        ):
            observed.add("PK-2")
        else:
            problems.append(
                f"{tag}: spec_hash baseline {bm.get('spec_hash')!r} "
                f"vs Shape {sm.get('spec_hash')!r}"
            )
    elif spec_file is not None:
        problems.append(f"{tag}: Shape's spec_hash does not hash the spec file")
    if bm.get("outputs") != sm.get("outputs"):
        if bm.get("outputs") == {}:
            observed.add("PK-3")
        else:
            problems.append(
                f"{tag}: outputs baseline {bm.get('outputs')} vs Shape {sm.get('outputs')}"
            )
    observed.add("ID-1")


# ─────────────────────────────────────────────────────────────────────────────
# T-21 (a)-(f)
# ─────────────────────────────────────────────────────────────────────────────


def written_tables(out: Path) -> dict[str, pd.DataFrame]:
    """The Parquet tables a pack run wrote under ``out``, by table name."""
    files = sorted(p for p in out.rglob("*.parquet"))
    return {p.stem: pd.read_parquet(p) for p in files}


def arrow_types(out: Path) -> dict[str, dict[str, str]]:
    return {
        p.stem: {f.name: DV._norm_type(f.type) for f in pq.read_schema(p)}
        for p in sorted(out.rglob("*.parquet"))
    }


def t21(
    base_dirs: dict[int, Path],
    shape_dir: Path,
    shape_tamper: bool,
    base_order: list[str],
    shape_order: list[str],
) -> tuple[list[str], dict[str, Any]]:
    """Clauses (a)-(f) over the tables the pack run wrote. Returns (flagged, report)."""
    raw = DV.load_schema_json(common.DOMAIN)
    pools = DV.Pools(common.DOMAIN)
    SP = written_tables(base_dirs[common.REF_SEED])
    sp_types = arrow_types(base_dirs[common.REF_SEED])
    IM = written_tables(shape_dir)
    im_types = arrow_types(shape_dir)
    if shape_tamper:
        IM = tamper(IM, raw)
    tables = list(SP)
    flagged: list[str] = []
    report: dict[str, Any] = {"tables": {}, "columns": 0, "columns_equivalent": 0}
    # (a)
    if set(SP) != set(IM):
        flagged.append(f"(a) tables differ: baseline {sorted(SP)} vs Shape {sorted(IM)}")
        return flagged, report
    if [t for t in base_order if t in SP] != [t for t in shape_order if t in IM]:
        flagged.append(f"(a) table order differs: baseline {base_order} vs Shape {shape_order}")
    fks_all = DV.foreign_keys(raw)
    fks = [fk for fk in fks_all if fk[0] in SP and fk[2] in SP]
    per_seed: dict[Any, list[Any]] = {}
    fan_sp = DV.fanout(SP, fks)
    for seed, d in base_dirs.items():
        if seed == common.REF_SEED:
            continue
        BL = written_tables(d)
        for tn in tables:
            for c in SP[tn].columns:
                if c in BL[tn].columns:
                    per_seed.setdefault((tn, c), []).append(
                        DV.baseline_distances(SP[tn][c], BL[tn][c])
                    )
        fan_bl = DV.fanout(BL, fks)
        for k, v in fan_sp.items():
            per_seed.setdefault(("fanout", k), []).append(
                DV.ks(v["_counts"].astype(float), fan_bl[k]["_counts"].astype(float))
            )
    B = {k: (DV.merge_baselines(v) if k[0] != "fanout" else max(v)) for k, v in per_seed.items()}
    for tn in tables:
        sp, im = SP[tn], IM[tn]
        tr: dict[str, Any] = {"rows": {"baseline": len(sp), "shape": len(im)}, "columns": {}}
        if list(sp.columns) != list(im.columns):
            flagged.append(f"(a) {tn}: column names/order differ")
        if len(sp) != len(im):
            flagged.append(f"(a) {tn}: rows {len(sp)} vs {len(im)}")
        for c in sp.columns:
            if c not in im.columns:
                flagged.append(f"(b) {tn}.{c}: missing in Shape")
                report["columns"] += 1
                continue
            gen = raw["tables"][tn]["columns"][c]["generator"]
            cr = DV.compare_column(
                sp[c], im[c], B[(tn, c)], pools.pool_for(gen), pools.component_fn(gen)
            )
            cr["arrow_type_match"] = sp_types[tn].get(c) == im_types[tn].get(c)
            # A deliberate difference from domain_1to1/domain_differences.py (the one list, with
            # its reason): accepted only when the column fails exactly the listed checks and
            # meets the replacement rule; anything else still fails.
            allowed = DV.DELIBERATE.get((common.DOMAIN, tn, c))
            deliberate = False
            if allowed is not None:
                failing = tuple(k for k, v in cr["checks"].items() if not v)
                if failing == allowed.fails and allowed.accepts(im[c]):
                    cr["equivalent"] = True
                    deliberate = True
            cr["equivalent"] = cr["equivalent"] and cr["arrow_type_match"]
            tr["columns"][c] = {
                "equivalent": cr["equivalent"],
                "checks": cr["checks"],
                "deliberate": deliberate,
            }
            report["columns"] += 1
            report["columns_equivalent"] += int(cr["equivalent"])
            if not cr["equivalent"]:
                failed = [k for k, v in cr["checks"].items() if not v]
                flagged.append(
                    f"(b-e) {tn}.{c}: failed {failed}" + ("" if cr["arrow_type_match"] else " type")
                )
        report["tables"][tn] = tr
    # (f)
    fk_im = DV.fk_checks(IM, fks)
    for k, v in fk_im.items():
        if v["integrity"] < 1.0:
            flagged.append(f"(f) FK integrity {k}: {v['integrity']:.6f}")
    fi = DV.fanout(IM, fks)
    for k in fan_sp:
        cs, ci = fan_sp[k]["_counts"], fi[k]["_counts"]
        d = DV.ks(cs.astype(float), ci.astype(float))
        tol = max(DV.ks_crit(len(cs), len(ci)), 1.5 * B[("fanout", k)] + DV.FANOUT_TOL_ABS)
        if not d <= tol:
            flagged.append(f"(f) fan-out {k}: KS {d:.4f} > tol {tol:.4f}")
    report["fk_checked"] = [f"{c}.{cc}->{p}.{pc_}" for c, cc, p, pc_ in fks]
    return flagged, report


def tamper(tables: dict[str, pd.DataFrame], raw: dict[str, Any]) -> dict[str, pd.DataFrame]:
    """Shape's output made deliberately wrong (the negative control)."""
    out = {t: df.copy() for t, df in tables.items()}
    fk_cols = {(c, cc) for c, cc, _, _ in DV.foreign_keys(raw)}
    for t, df in out.items():
        pk = set(raw["tables"][t]["primary_key"])
        done_num = done_cat = False
        for c in df.columns:
            if (t, c) in fk_cols or c in pk:
                continue
            if not done_num and pd.api.types.is_float_dtype(df[c]):
                df[c] = df[c] * 1.5 + 100.0
                done_num = True
            elif not done_cat and df[c].dtype == object and df[c].nunique() < 40:
                df[c] = "tampered"
                done_cat = True
    for t, c in sorted(fk_cols):
        if t in out and c in out[t].columns:
            col = pd.to_numeric(out[t][c], errors="coerce")
            if col.notna().any():
                col = col.copy()
                col.iloc[: max(1, len(col) // 3)] = 10**9
                out[t][c] = col
    return out


# ─────────────────────────────────────────────────────────────────────────────
# probes: the allow-listed defects, shown on small packs (not reference inputs)
# ─────────────────────────────────────────────────────────────────────────────

PROBE_BASE = """\
pack_version: 1
id: probe
kind: file_drop
domain: retail
description: probe
fabric_targets:
  lakehouse_files_root: Files/landing/retail
file_drop:
  formats: [parquet]
  entities: [order, order_line]
validation:
  required_gates: [schema_conformance]
"""


def probe_texts() -> dict[str, str]:
    escape = (OUT / "probes" / "escape_target").as_posix()
    return {
        "PK-1": PROBE_BASE,
        "PK-4": PROBE_BASE.replace("[schema_conformance]", "[schema_conformanse]"),
        "PK-5": PROBE_BASE.replace("Files/landing/retail", escape),
        "PK-6": "",
    }


def run_probes(observed: set[str]) -> list[str]:
    """Run each probe in both tools; the baseline shows the defect, Shape does not."""
    problems: list[str] = []
    root = OUT / "probes"
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)
    for pid, text in probe_texts().items():
        f = root / f"{pid}.yaml"
        f.write_text(text)
        base = run_json(
            [
                str(REFENGINE_PY),
                str(HERE / "baseline_worker.py"),
                "--probe",
                "--input",
                str(f),
                "--kind",
                "pack",
                "--seeds",
                "42",
                "--out",
                str(root / f"{pid}_baseline"),
            ]
        )
        r = shape_cli(
            "pack",
            "run",
            str(f),
            "--scale",
            common.SCALE,
            "-o",
            str(root / f"{pid}_shape"),
            "--json",
        )
        shape = (
            json.loads(r.stdout)
            if r.stdout.strip().startswith("{")
            else {"exit": r.returncode, "stderr": r.stderr}
        )
        if pid == "PK-1":
            lists = base["run"]["manifest_tables"]
            if not any(Path(p).stem == "order_line" for p in lists["order"]):
                problems.append("probe PK-1: the baseline no longer lists order_line under order")
            own = shape["manifest"]["tables"]["order"]["file_paths"]
            if [Path(p).stem for p in own] != ["order"]:
                problems.append(f"probe PK-1: Shape's order lists {own}")
        elif pid == "PK-4":
            if base["run"]["gates"] != {"schema_conformanse": True} or not base["run"]["success"]:
                problems.append(f"probe PK-4: baseline did not pass an unknown gate: {base['run']}")
            if (
                shape["success"]
                or shape["gates"] != {"schema_conformanse": False}
                or r.returncode != 1
            ):
                problems.append(f"probe PK-4: Shape accepted an unknown gate (exit {r.returncode})")
        elif pid == "PK-5":
            outside = OUT / "probes" / "escape_target"
            if not (outside.exists() and any(outside.rglob("*.parquet"))):
                problems.append(
                    "probe PK-5: the baseline no longer writes outside its output directory"
                )
            shutil.rmtree(outside, ignore_errors=True)
            if r.returncode == 0 or outside.exists():
                problems.append(
                    f"probe PK-5: Shape wrote or accepted an "
                    f"absolute landing root (exit {r.returncode})"
                )
        elif pid == "PK-6":
            if base["exception"] is None:
                problems.append("probe PK-6: the baseline no longer fails on an empty pack file")
            if r.returncode != 2 or "empty" not in r.stderr:
                problems.append(
                    f"probe PK-6: Shape did not report an empty "
                    f"pack file (exit {r.returncode}): {r.stderr[:200]}"
                )
        observed.add(pid)
    return problems


# ─────────────────────────────────────────────────────────────────────────────
# driver
# ─────────────────────────────────────────────────────────────────────────────


def table_order(manifest: dict[str, Any], out: Path) -> list[str]:
    written = {p.stem for p in out.rglob("*.parquet")}
    return [t for t in manifest["tables"] if t in written]


def check_input(
    name: str, kind: str, control: bool, observed: set[str]
) -> tuple[list[str], dict[str, Any]]:
    problems: list[str] = []
    seeds = [common.REF_SEED, *common.BASELINE_SEEDS]
    base = baseline_run(name, kind, seeds)
    chaos_on = bool((base["loaded"].get("chaos") or {}).get("enabled"))
    shape_load = run_json(
        [
            str(SHAPE_PY),
            str(HERE / "shape_worker.py"),
            "--input",
            str(common.FIXTURES / name),
            "--kind",
            kind,
        ]
    )
    if unwrap_chaos_config(base["loaded"]):
        observed.add("PK-10")
    compare_loaded(base["loaded"], shape_load["loaded"], problems, name)
    if kind == "spec":
        compare_loaded(
            base["pack_structure"], shape_load["pack_structure"], problems, f"{name} pack"
        )
        # The baseline has no spec validator: its verdict is the referenced pack's. Shape's
        # spec validation prefixes the pack's messages.
        spec_val = shape_load["spec_validation"]
        pack_errors = [e[len("pack: ") :] for e in spec_val["errors"] if e.startswith("pack: ")]
        pack_warnings = [w[len("pack: ") :] for w in spec_val["warnings"] if w.startswith("pack: ")]
        compare_validation(
            base["validation"],
            {"is_valid": not pack_errors, "errors": pack_errors, "warnings": pack_warnings},
            problems,
            f"{name} pack validation",
        )
        other = [e for e in spec_val["errors"] if not e.startswith("pack: ")]
        if other:
            problems.append(f"{name}: spec validation errors {other}")
    else:
        compare_validation(base["validation"], shape_load["validation"], problems, name)
    # Run at fabric_demo, seed 42, both tools.
    shape_doc, shape_out = shape_run(name, kind, common.REF_SEED)
    if shape_doc.get("exit_code") != 0:
        problems.append(
            f"{name}: shape pack run exited {shape_doc['exit_code']}: {shape_doc.get('errors')}"
        )
    manifest = find_manifest(shape_out)
    compare_runs(
        name,
        base["runs"][str(common.REF_SEED)],
        shape_doc,
        manifest,
        shape_out,
        common.FIXTURES / name if kind == "spec" else None,
        problems,
        observed,
        drop_manifest_key="lakehouse_id" if control else None,
        chaos_on=chaos_on,
    )
    report: dict[str, Any] = {"input": name, "kind": kind, "chaos": chaos_on}
    if chaos_on:
        report["t21"] = "skipped: the input enables chaos (T-21 covers packs without chaos)"
        if not shape_doc["manifest"]["chaos"]:
            problems.append(f"{name}: chaos is enabled but the manifest records no chaos")
        return problems, report
    base_dirs = {s: OUT / "baseline" / name / f"seed{s}" for s in seeds}
    shape_t21_out = shape_run(name, kind, common.SHAPE_SEED)[1]
    flagged, t21_report = t21(
        base_dirs,
        shape_t21_out,
        control,
        table_order(base["runs"][str(common.REF_SEED)]["manifest"], base_dirs[common.REF_SEED]),
        table_order(find_manifest(shape_t21_out), shape_t21_out),
    )
    problems.extend(f"{name}: T-21 {f}" for f in flagged)
    report["t21"] = t21_report
    return problems, report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "--negative-control",
        action="store_true",
        help="tamper with Shape and require the checks to fail",
    )
    ap.add_argument("--out", help="report JSON (default: $BENCH_OUT_DIR/pack_1to1/report.json)")
    a = ap.parse_args(argv)
    t0 = time.time()
    observed: set[str] = set()
    all_problems: list[str] = []
    reports: list[dict[str, Any]] = []
    try:
        for name, kind in common.INPUTS:
            print(f"== {name} ({kind})", file=sys.stderr, flush=True)
            problems, report = check_input(name, kind, a.negative_control, observed)
            all_problems.extend(problems)
            reports.append(report)
        all_problems.extend(run_probes(observed))
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    # Every key of the baseline's manifests, gate names and categories included, appears in some
    # Shape manifest of this run.
    for key in sorted(baseline_manifest_keys(True) - SHAPE_KEYS):
        all_problems.append(f"no Shape manifest of these runs has the baseline key {key}")
    unexplained_allowed = sorted(
        k
        for k, v in common.ALLOWED.items()
        if v.get("observed", "yes") == "yes" and k not in observed
    )
    summary = {
        "mode": "negative-control" if a.negative_control else "verify",
        "inputs": reports,
        "problems": all_problems,
        "allow_list": {k: {**v, "observed": k in observed} for k, v in common.ALLOWED.items()},
        "seconds": round(time.time() - t0, 1),
    }
    out = (
        Path(a.out)
        if a.out
        else OUT / ("report_negative_control.json" if a.negative_control else "report.json")
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(common.dumps(summary))
    if a.negative_control:
        kinds = {
            "(a)": any("(a)" in p for p in all_problems),
            "(b-e)": any("(b-e)" in p for p in all_problems),
            "(f)": any("(f)" in p for p in all_problems),
            "manifest key": any("lacks the baseline key" in p for p in all_problems),
        }
        for p in all_problems[:12]:
            print("flagged:", p)
        print(f"{len(all_problems)} problems flagged; by clause: {kinds}")
        if all(kinds[k] for k in ("(b-e)", "(f)", "manifest key")):
            print("NEGATIVE CONTROL OK: the harness fails on tampered output")
            return 0
        print("NEGATIVE CONTROL FAILED: a tampered output went undetected", file=sys.stderr)
        return 1
    for p in all_problems:
        print("FAIL:", p)
    if unexplained_allowed:
        print(f"note: allow-listed differences not observed on these inputs: {unexplained_allowed}")
    cols = sum(r["t21"]["columns"] for r in reports if isinstance(r.get("t21"), dict))
    eq = sum(r["t21"]["columns_equivalent"] for r in reports if isinstance(r.get("t21"), dict))
    print(
        f"{len(common.INPUTS)} inputs, T-21 columns {eq}/{cols}, "
        f"{len(all_problems)} problems, {summary['seconds']}s"
    )
    print("PASS" if not all_problems else "FAIL")
    return 0 if not all_problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
