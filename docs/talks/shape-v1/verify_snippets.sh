#!/usr/bin/env bash
# Checks what the October 3, 2026 talk claims (SCRIPT.md, DEMO.md). Three parts:
#
#   Part 1, CLAIMS (asserted): every runnable snippet on a slide, and every "runs today"
#           statement the slides make about the code and docs on this checkout.
#           Any failure exits 1.
#   Part 2, TALK TEXT (asserted with REQUIRE_READY=1): no "TBD" and no PENDING markers left
#           in the talk files, and slide 22's live profile (C3) actually ran (not skipped).
#   Part 3, PLANNED (information only, never a gate): the "how it will work" items R1-R9,
#           the owner's Fabric dry run (R11) and PyPI (F2). If a planned item reports READY,
#           the talk still calls it planned: update the slides before claiming it.
#
#   source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
#   source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/*/domain_1to1/generate.py \
#       --impl reference_port --domain retail --scale medium --seed 42
#   PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh
#   REQUIRE_READY=1 PY=... bash docs/talks/shape-v1/verify_snippets.sh     # delivery gate
#
# Inputs: DEMO_DATA (default $BENCH_DATA_DIR/demo, from demo/make_data.py) and PROD_DATA
# (default $BENCH_OUT_DIR/reference_port/retail/medium/seed42, the "production" stand-in).
# Works in a scratch directory; writes nothing in the repo.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TALK="$REPO/docs/talks/shape-v1"
PY="${PY:-python}"
BIN="$(dirname "$("$PY" -c 'import sys; print(sys.executable)')")"
SHAPE="$BIN/shape"
DEMO_DATA="${DEMO_DATA:-${BENCH_DATA_DIR:-$HOME/bench-data}/demo}"
PROD_DATA="${PROD_DATA:-${BENCH_OUT_DIR:-$HOME/bench-out}/reference_port/retail/medium/seed42}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

test -f "$DEMO_DATA/day1/orders.parquet" || { echo "no demo data in $DEMO_DATA (run demo/make_data.py)"; exit 2; }
test -f "$PROD_DATA/_SUCCESS" || { echo "no prod stand-in in $PROD_DATA (run generate.py, see header)"; exit 2; }
cp "$DEMO_DATA/day1/orders.parquet" "$WORK/orders_day1.parquet"
cp "$DEMO_DATA/day2/orders.parquet" "$WORK/orders_day2.parquet"
mkdir -p "$WORK/contracts" "$WORK/prod" && cp "$REPO"/demo/contracts/*.json "$WORK/contracts/"
cp "$PROD_DATA"/*.parquet "$WORK/prod/"
cd "$WORK"

echo "== shape $("$PY" -c 'import shape; print(shape.__version__)') at $("$PY" -c 'import shape, os; print(os.path.dirname(shape.__file__))')"
echo "== repo $(git -C "$REPO" rev-parse --short HEAD)"
echo
echo "######## PART 1: CLAIMS (asserted) ########"

echo "== slide 10: profile a whole schema; slide 11: the report; slide 20: raw profiles hold real values"
"$PY" - <<'EOF'
# --- slide 10 ---------------------------------------------------------------
import shape
from pathlib import Path

prod = {f.stem: str(f) for f in Path("prod").glob("*.parquet")}
p = shape.profile(prod, name="retail")
shape.save(p, "retail_prod.shape")
for r in p.summary()["relationships"]:
    print(r["child"], r["child_columns"], "->", r["parent"])

rels = {(r["child"], r["child_columns"][0], r["parent"]) for r in p.summary()["relationships"]}
assert ("order", "customer_id", "customer") in rels
assert ("order_line", "order_id", "order") in rels
assert ("order_line", "product_id", "product") in rels
assert ("return", "order_id", "order") in rels
assert len(p.summary()["tables"]) == 9
assert sum(t["row_count"] for t in p.summary()["tables"].values()) == 1_965_400   # N-75
assert len(rels) == 8   # NUMBERS.md N-76
assert shape.load("retail_prod.shape").to_dict() == p.to_dict()

# --- slide 11 ---------------------------------------------------------------
html = p.to_html()
assert "http://" not in html and "https://" not in html
print("report ok")

# --- slide 20: a raw profile contains real values ---------------------------
import zipfile, pyarrow.parquet as pq
raw = zipfile.ZipFile("retail_prod.shape").read("profile.json").decode()
emails = [e for e in pq.read_table("prod/customer.parquet").column("email").to_pylist() if e]
found = sum(1 for e in emails[:5000] if e in raw)
print(f"raw .shape contains {found} of the first 5000 customer emails")
assert found > 0, "slide 20 says raw profiles contain real values; update the slide"
EOF

echo "== slide 20: the README and the Fabric runbook carry the warnings the slide quotes"
grep -q "Treat a \`.shape\` file, its HTML report and its JSON summary as you would the source data" "$REPO/README.md" \
    || { echo "README warning not found (slide 20 quotes it)"; exit 1; }
grep -q "up to the 500" "$REPO/README.md" || { echo "README: '500 values' wording not found"; exit 1; }
grep -q "Grant access as you would to the source tables" "$REPO/integrations/fabric/RUNBOOK.md" \
    || { echo "runbook warning not found (slide 20 quotes it)"; exit 1; }
echo "docs warnings ok"

echo "== slides 17-18: check and diff (day 1 vs day 2)"
"$PY" - <<'EOF'
import shape

p = shape.profile("orders_day1.parquet", name="orders")
shape.save(p, "orders_day1.shape")
assert shape.check(p, "contracts/orders.json").passed

# --- slide 17 ---------------------------------------------------------------
today = shape.profile("orders_day2.parquet", name="orders")
r = shape.check(today, "contracts/orders.json")
print(r.passed)
for v in r.violations:
    print(v["column"], v["rule"], v["observed"])
assert r.passed is False
assert {(v["column"], v["rule"]) for v in r.violations} == {("status", "allowed_values"), ("order_total", "max")}
assert 7189.882 in [v["observed"] for v in r.violations]

# --- slide 18 ---------------------------------------------------------------
d = shape.diff(shape.load("orders_day1.shape"), today)
assert ("order_total", "mean_shift") not in [(c["column"], c["kind"]) for c in d.changes]
d = shape.diff(shape.load("orders_day1.shape"), today,
               thresholds={"mean_shift_std": 0.25})
for c in d.changes:
    print(c["column"], c["kind"], c["severity"])
kinds = [(c["column"], c["kind"], c["severity"]) for c in d.changes]
assert ("order_total", "mean_shift", "medium") in kinds
assert ("status", "new_categorical_values", "low") in kinds
EOF

echo "== slide 16: what the Fabric notebook does with a lakehouse table"
"$PY" - <<'EOF'
import pyarrow.parquet as pq
from deltalake import write_deltalake
write_deltalake("Tables/orders_day1", pq.read_table("orders_day1.parquet"))

import shape
p = shape.profile("Tables/orders_day1", name="orders_day1")
r = shape.check(p, "contracts/orders.json")
print(p.summary()["name"], p.summary()["row_count"], r.passed)
assert p.summary()["columns"]["order_total"]["max"] == 5135.63 and r.passed
EOF
for f in notebooks/shape_profile.ipynb notebooks/shape_profile_spark.ipynb pipelines/shape_gate_notebook.DataPipeline udf/function_app.py; do
    test -e "$REPO/integrations/fabric/$f" || { echo "slide 14/16 names integrations/fabric/$f, which is missing"; exit 1; }
done
grep -q "DRIVER_ROW_LIMIT = 5_000_000" "$REPO/integrations/fabric/RUNBOOK.md" || { echo "slide 14: 5,000,000-row driver cap not in runbook"; exit 1; }
echo "fabric items ok"

echo "== slide 19 and C2-local: CLI and exit codes"
run() { echo "\$ $*"; set +e; "$@" > out.txt 2>&1; rc=$?; set -e; head -c 300 out.txt; echo; echo "exit $rc"; }
expect() { [ "$rc" -eq "$1" ] || { echo "expected exit $1, got $rc"; exit 1; }; }
run "$SHAPE" profile orders_day1.parquet -o day1.shape --html day1.html --json day1.json; expect 0
run "$SHAPE" profile orders_day2.parquet -o day2.shape; expect 0
run "$SHAPE" check day1.shape contracts/orders.json; expect 0
run "$SHAPE" check day2.shape contracts/orders.json --json result.json; expect 1
run "$SHAPE" diff day1.shape day2.shape --fail-on-drift; expect 1
run "$SHAPE" check day1.shape contracts/no-such.json; expect 2
run "$SHAPE" check no-such.shape contracts/orders.json; expect 2      # F1, fixed in b2dd663
run "$SHAPE" diff no-such.shape day2.shape; expect 2                  # F1, fixed in b2dd663

echo "== slide 22 (C3): profile the 1M-row, 20-column file from the CLI"
c3_ran=0
test -f "$DEMO_DATA/day1/d2.parquet" && cp "$DEMO_DATA/day1/d2.parquet" d2.parquet
if [ -f d2.parquet ]; then
    "$PY" - <<'EOF'
import pyarrow.parquet as pq
md = pq.ParquetFile("d2.parquet").metadata
print(md.num_rows, "rows x", md.num_columns, "columns")
assert md.num_rows == 1_000_000 and md.num_columns == 20   # slides 13 and 22
EOF
    run "$SHAPE" profile d2.parquet -o d2.shape; expect 0
    grep -q '"written": "d2.shape"' out.txt || { echo "slide 22 output line changed"; exit 1; }
    c3_ran=1
else
    echo "SKIPPED: no d2.parquet in $DEMO_DATA/day1"
fi
run "$SHAPE" version; expect 0

echo "== slides 13, 21, 23: the quoted numbers are the committed measurements"
"$PY" - "$REPO" <<'EOF'
import json, math, sys
repo = sys.argv[1]
d = json.load(open(f"{repo}/benchmarks/baselines/2026-09-30-product/product_bench.json"))
assert not any("bounded" in k or "engine" in k for k in d), "bounded/engine rows now exist: update slides 12, 21, 27"
script = open(f"{repo}/docs/talks/shape-v1/SCRIPT.md").read()
def t(x, nd=2):
    f = 10 ** nd
    return f"{math.floor(x * f) / f:.{nd}f}"
for key, r in d["profile"].items():
    assert r["output_identical_across_runs"], key
    if key == "d2.csv":
        continue
    for v in (t(r["median_s"]), f"{math.floor(r['rows_per_s']):,}", f"{math.floor(r['peak_rss_mb']):,}"):
        assert v in script, f"{key}: {v} is not in SCRIPT.md"
su = d["startup"]
for v in (math.floor(su["python_bare_median_s"] * 1000), math.floor(su["import_shape_median_s"] * 1000),
          math.floor(su["cli_version_cmd_median_s"] * 1000)):
    assert f"{v} ms" in script, f"start-up {v} ms is not in SCRIPT.md"
print("slide numbers match product_bench.json")
EOF
"$PY" "$REPO/demo/build_benchmark_sheet.py" --check && echo "demo/BENCHMARKS.md is up to date"

echo
echo "######## PART 2: TALK TEXT ########"
text_bad=0
for f in SCRIPT.md DEMO.md OUTLINE.md NUMBERS.md; do
    if grep -n 'TBD\|⟦PENDING' "$TALK/$f"; then echo "  ^ $f still has TBD or PENDING markers"; text_bad=1; fi
done
[ $text_bad -eq 0 ] && echo "no TBD or PENDING markers in SCRIPT.md, DEMO.md, OUTLINE.md, NUMBERS.md"
[ $c3_ran -eq 1 ] || { echo "slide 22's live profile (C3) was skipped"; text_bad=1; }

echo
echo "######## PART 3: PLANNED (information only; not gates) ########"
report() { printf '%-5s %-8s %s\n' "$1" "$2" "$3"; }
planned() {  # planned ID IS_READY DETAIL
    if [ "$2" = 1 ]; then report "$1" READY "$3 -- the talk still says planned: update slides 25-27 first"
    else report "$1" planned "$3"; fi
}

set +e; "$SHAPE" profile validate --safe retail_prod.shape >/dev/null 2>&1; rc=$?; set -e
planned R1 $([ $rc -eq 0 ] || [ $rc -eq 1 ] && echo 1 || echo 0) "safe-profile validation, P7-01 (probe exit $rc)"
planned R2 0 "k-anonymity and suppression, P7-02 (no probe until P7-01 names the export command)"
set +e; "$SHAPE" plan retail_prod.shape >/dev/null 2>&1; rc=$?; set -e
planned R3 $([ $rc -eq 0 ] && echo 1 || echo 0) "shape plan, P4-08 (probe exit $rc)"
planned R4 $("$SHAPE" generate --help 2>/dev/null | grep -q -- '--from' && echo 1 || echo 0) "generate from a shape, P4-08"
planned R5 $("$SHAPE" fidelity --help 2>/dev/null | grep -qi 'tier\|threshold' && echo 1 || echo 0) "fidelity report, P4-09"
set +e; BENCH_OUT_DIR="$WORK/gen" "$PY" "$REPO"/benchmarks/*/domain_1to1/generate.py \
    --impl shape --domain retail --scale small --seed 1042 >/dev/null 2>&1; rc=$?; set -e
planned R6 $([ $rc -eq 0 ] && echo 1 || echo 0) "retail through the product engine, P4-07 (probe exit $rc)"
planned R7 0 "engine and bounded-mode timings and memory, G1/G4 (none measured; slides 21 and 27 say so)"
planned R8 $(grep -rqs mapInArrow "$REPO/integrations/fabric/notebooks/" && echo 1 || echo 0) "distributed Spark profiling, PF-02"
planned R9 $(ls "$REPO"/integrations/fabric/pipelines/ | grep -qi gen && echo 1 || echo 0) "generation pipelines, PF-06"

if grep -q 'TBD' "$REPO/demo/LIVE_TIMINGS.md"; then
    report R11 "not done" "owner's Fabric dry run: LIVE_TIMINGS.md still has TBD cells -> Path B (C2-local, no Fabric timings)"
else
    report R11 done "LIVE_TIMINGS.md filled in -> Path A allowed (quote rows exactly)"
fi
if pypi=$(curl -sS -m 10 -o /dev/null -w '%{http_code}' https://pypi.org/pypi/sqllocks-shape/json 2>/dev/null); then
    [ "$pypi" = 200 ] && report F2 "on PyPI" "pypi.org has sqllocks-shape -> slide 30: pip install sqllocks-shape" \
                      || report F2 "not on PyPI" "pypi.org HTTP $pypi -> slide 30: install from the repo"
else
    report F2 unknown "pypi.org not reachable from here; check by hand"
fi

echo
echo "CLAIMS OK"
if [ "${REQUIRE_READY:-0}" = 1 ]; then
    [ $text_bad -eq 0 ] || { echo "NOT READY TO DELIVER (talk text)"; exit 1; }
    echo "READY TO DELIVER (every claim asserted; planned items are not gates)"
fi
