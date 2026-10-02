"""The codes lane's authoritative code sets (``sqllocks-shape-healthcare-codes``), used when
installed and built (``shape healthcare-codes fetch ...``).

The domain generates from its own curated seed sets (``icd10cm.py``, ``reference.py``,
``drugs.py``) so it runs offline.  This adapter does three things with the real assets:

* :meth:`AssetCodes.cross_check_seeds` checks every seed code against them (existence, billable
  flag, effective and end dates, age and sex edits, HCC categories);
* :meth:`AssetCodes.validate_tables` validates generated tables against them (acceptance item 5
  on the real FDA directory and the real ICD-10-CM release history);
* :class:`FdaNdcDirectory` is an :class:`~shape_domains.healthcare_payer.drugs.NdcDirectory` built
  from the FDA NDC asset, so fills carry real NDCs marketed on the fill date.

Nothing here imports the codes plugin at import time.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .drugs import DRUGS, NdcRecord


class CodesUnavailable(RuntimeError):
    """The codes plugin or one of its assets is not installed or not built."""


@dataclass(slots=True)
class AssetCodes:
    """Loaded assets (any may be ``None`` when not built)."""

    icd: Any  # CodeSet
    pcs: Any = None
    hcpcs: Any = None
    ndc: Any = None
    pos: Any = None
    hcc: Any = None
    edits: Any = None
    ccsr: Any = None
    mod: Any = None  # the shape_healthcare_codes module

    @classmethod
    def load(cls, *systems: str) -> AssetCodes:
        """Load icd10cm and any other built assets (pcs, hcpcs2, ndc, pos, hcc, edits)."""
        try:
            import shape_healthcare_codes as sc
        except ImportError as exc:  # pragma: no cover - exercised where the plugin is absent
            raise CodesUnavailable(
                "install sqllocks-shape-healthcare-codes and run `shape healthcare-codes fetch`"
            ) from exc
        wanted = systems or (
            "icd10cm",
            "icd10pcs",
            "hcpcs2",
            "ndc",
            "pos",
            "hcc",
            "mce_edits",
            "ccsr",
        )
        loaded: dict[str, Any] = {}
        from shape_healthcare_codes.store import read_table

        plain = {"hcc", "mce_edits", "ccsr"}  # tables that are not code sets (no base columns)
        for name in wanted:
            try:
                loaded[name] = read_table(name) if name in plain else sc.load(sc.CodeSystem(name))
            except sc.AssetMissing:
                loaded[name] = None
        if loaded.get("icd10cm") is None:
            raise CodesUnavailable("the icd10cm asset is not available")
        edits = None
        if loaded.get("mce_edits") is not None:
            from shape_healthcare_codes.validators import EditIndex

            edits = EditIndex.from_table(loaded["mce_edits"])
        return cls(
            loaded["icd10cm"],
            loaded.get("icd10pcs"),
            loaded.get("hcpcs2"),
            loaded.get("ndc"),
            loaded.get("pos"),
            loaded.get("hcc"),
            edits,
            loaded.get("ccsr"),
            sc,
        )

    # ---- single-code checks -----------------------------------------------------------------
    def icd_ok(self, code: str, day: dt.date) -> bool:
        return bool(self.mod.icd10cm_valid_billable(self.icd, code, day))

    def pcs_ok(self, code: str, day: dt.date) -> bool:
        return self.pcs is not None and bool(self.pcs.is_valid(code, day, leaf_only=True))

    def hcpcs_ok(self, code: str) -> bool:
        return self.hcpcs is not None and code in self.hcpcs

    def ndc_index(self) -> Any:
        return self.mod.NdcIndex.from_table(self.ndc.table)

    def edit_violations(self, code: str, age: int, sex: str) -> list[str]:
        if self.edits is None:
            return []
        from shape_healthcare_codes.validators import age_sex_violations

        return list(age_sex_violations(self.edits, code, age, sex))

    # ---- seeds ------------------------------------------------------------------------------
    def cross_check_seeds(self) -> list[str]:
        """Disagreements between the seed code sets and the assets (empty: they agree)."""
        from .icd10cm import FAR, ICD10CM
        from .reference import HCC_OF, PCS, POS
        from .services import SERVICES

        problems: list[str] = []
        for code, e in ICD10CM.items():
            rec = self.icd.get(code)
            if rec is None:
                problems.append(f"icd10cm {code}: not in the asset")
                continue
            mid = e.effective if e.effective > dt.date(2016, 10, 1) else dt.date(2017, 1, 1)
            last = e.end if e.end != FAR else dt.date(2026, 1, 1)
            for day in {mid, last}:
                if e.billable and not self.icd_ok(code, day):
                    problems.append(f"icd10cm {code}: seed says billable on {day}, asset disagrees")
                if not e.billable and self.icd_ok(code, day):
                    problems.append(f"icd10cm {code}: seed says header on {day}, asset says leaf")
            if e.end != FAR and self.icd_ok(code, e.end + dt.timedelta(days=1)):
                problems.append(f"icd10cm {code}: valid after the seed end date {e.end}")
            if e.effective > dt.date(2016, 10, 1) and self.icd_ok(
                code, e.effective - dt.timedelta(days=1)
            ):
                problems.append(
                    f"icd10cm {code}: valid before the seed effective date {e.effective}"
                )
        if self.pcs is not None:
            for code in PCS:
                if not self.pcs_ok(code, dt.date(2023, 1, 1)):
                    problems.append(f"icd10pcs {code}: not a valid leaf on 2023-01-01")
        if self.hcpcs is not None:
            for s in SERVICES.values():
                if s.hcpcs and s.hcpcs not in self.hcpcs:
                    problems.append(f"hcpcs {s.hcpcs} ({s.key}): not in the asset")
        if self.pos is not None:
            for code in POS:
                if code not in self.pos:
                    problems.append(f"pos {code}: not in the asset")
        if self.hcc is not None:
            problems += self._hcc_problems(HCC_OF)
        return problems

    def _hcc_problems(self, mapping: dict[str, int]) -> list[str]:
        table = self.hcc
        if not {"code", "model", "hcc"} <= set(table.column_names):
            return []
        by: dict[str, set[str]] = defaultdict(set)
        for r in table.to_pylist():
            if str(r["model"]).upper().endswith("V28") and str(r["model"]).startswith("CMS-HCC"):
                by[r["code"]].add(str(r["hcc"]).strip())
        out = []
        for code, hcc in mapping.items():
            norm = self.mod.normalize_code(code)
            if str(hcc) not in by.get(norm, set()):
                out.append(
                    f"hcc {code}: seed maps to V28 HCC {hcc}, "
                    f"asset has {sorted(by.get(norm, set()))}"
                )
        return out

    # ---- tables -----------------------------------------------------------------------------
    def validate_tables(self, tables: dict[str, Any]) -> dict[str, Any]:
        """Check generated tables against the assets; the counts are the evidence for item 5."""
        claims = {r["claim_id"]: r for r in tables["medical_claim"].to_pylist()}
        members = {r["member_id"]: r for r in tables["member"].to_pylist()}
        bad_icd: list[str] = []
        bad_edit: list[str] = []
        n_dx = 0
        for r in tables["claim_diagnosis"].to_pylist():
            n_dx += 1
            c = claims[r["claim_id"]]
            if not self.icd_ok(r["icd10_code"], c["service_from_date"]):
                bad_icd.append(f"{r['diagnosis_code']} on {c['service_from_date']}")
            m = members[c["member_id"]]
            born = m["birth_date"]
            day = c["service_from_date"]
            age = day.year - born.year - ((day.month, day.day) < (born.month, born.day))
            v = self.edit_violations(r["icd10_code"], age, m["sex"])
            if v:
                bad_edit.append(f"{r['diagnosis_code']} {v} age {age} sex {m['sex']}")
        out: dict[str, Any] = {
            "diagnoses": n_dx,
            "icd10cm_not_valid_billable_on_date": len(bad_icd),
            "age_sex_edit_violations": len(bad_edit),
            "examples": (bad_icd + bad_edit)[:10],
        }
        if self.pcs is not None:
            procs = tables["claim_procedure"].to_pylist()
            out["icd10pcs_codes"] = len(procs)
            out["icd10pcs_not_valid"] = sum(
                1 for r in procs if not self.pcs_ok(r["icd10pcs_code"], r["procedure_date"])
            )
        if self.hcpcs is not None:
            codes = {
                r["procedure_code"]
                for r in tables["medical_claim_line"].to_pylist()
                if r["code_system"] == "HCPCS"
            }
            out["hcpcs_codes"] = len(codes)
            out["hcpcs_not_in_asset"] = sorted(c for c in codes if c not in self.hcpcs)
        if self.ndc is not None:
            index = self.ndc_index()
            fills = tables["pharmacy_claim"].to_pylist()
            out["fills"] = len(fills)
            out["ndc_not_marketed_on_fill_date"] = sum(
                1 for f in fills if not self.mod.ndc_marketed(index, f["ndc"], f["fill_date"])
            )
        return out


# -------------------------------------------------------------------------------------------------
def _digits(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except ValueError:
        return None


class FdaNdcDirectory:
    """Real NDC packages for the domain's drugs, from the FDA directory asset.

    Each drug is matched by its generic (nonproprietary) name, strength and dose form; the packages
    keep the FDA's marketing start and end dates.  Drugs the asset does not carry for a date return
    no package, and the fill is not generated (``rx.stats['no_ndc']``)."""

    source = "FDA NDC Directory (codes lane asset)"

    def __init__(self, table: Any, max_per_drug: int = 12) -> None:
        cols = set(table.column_names)
        self._by_drug: dict[str, list[NdcRecord]] = defaultdict(list)
        self._by_ndc: dict[str, NdcRecord] = {}
        rows = table.to_pylist()
        name_col = "nonproprietary_name" if "nonproprietary_name" in cols else "long_desc"
        for key, drug in DRUGS.items():
            generic = drug.name.lower().split("/")[0].split()[0]
            want_strength = _digits(drug.strength.split()[0])
            forms = [f.strip().lower() for f in drug.form.split(",")]
            brand_word = (drug.brand_name or "").lower().split()[:1]
            check_strength = "/dose" not in drug.strength  # pens: the asset lists per-mL strengths
            found: list[NdcRecord] = []
            for r in rows:
                if not r.get(name_col) or generic not in str(r[name_col]).lower():
                    continue
                if (
                    drug.brand
                    and drug.brand_name
                    and brand_word[0] not in str(r.get("proprietary_name", "")).lower()
                ):
                    continue
                if not any(f in str(r.get("dosage_form", "")).lower() for f in forms):
                    continue
                if check_strength and want_strength is not None and "strength" in cols:
                    have = _digits(str(r["strength"]).split()[0]) if r.get("strength") else None
                    if have is not None and abs(have - want_strength) > 1e-9:
                        continue
                found.append(self._record(key, r))
            found.sort(key=lambda x: (x.marketing_start, x.ndc))
            # a spread of labelers: take every n-th package
            step = max(1, len(found) // max_per_drug)
            for rec in found[::step][:max_per_drug]:
                self._by_drug[key].append(rec)
                self._by_ndc[rec.ndc] = rec

    @staticmethod
    def _record(key: str, r: dict[str, Any]) -> NdcRecord:
        far = dt.date(9999, 12, 31)
        units = (
            _digits(str(r.get("package_units", "")).split()[0]) if r.get("package_units") else None
        )
        return NdcRecord(
            r["code"],
            key,
            units or DRUGS[key].qty30,
            r.get("valid_from") or dt.date(1900, 1, 1),
            r.get("valid_to") or far,
            False,
            None,
            r.get("labeler_name") or r.get("labeler"),
        )

    def packages(self, drug_key: str, day: dt.date) -> list[NdcRecord]:
        return [
            r
            for r in self._by_drug.get(drug_key, [])
            if r.marketing_start <= day <= r.marketing_end
        ]

    def is_marketed(self, ndc: str, day: dt.date) -> bool:
        rec = self._by_ndc.get(ndc)
        return rec is not None and rec.marketing_start <= day <= rec.marketing_end

    def all_packages(self) -> list[NdcRecord]:
        return list(self._by_ndc.values())
