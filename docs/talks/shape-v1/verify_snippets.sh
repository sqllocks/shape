#!/usr/bin/env bash
# Runs every snippet that appears on a slide (SCRIPT.md, DEMO.md) and asserts what the
# slides show. It has two parts:
#
#   Part 1, RUNNABLE NOW: snippets that work on today's code. A failure exits 1.
#   Part 2, PENDING:      snippets for work that hasn't landed yet (READINESS.md). Each one
#                         is tried and reported as READY or PENDING. With REQUIRE_READY=1,
#                         any PENDING item exits 1: that is the go/no-go check before delivery.
#
#   source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
#   source scripts/env.sh && "$SHAPE_VENV/bin/python" benchmarks/vs_spindle/domain_1to1/generate.py \
#       --impl reference_port --domain retail --scale medium --seed 42
#   PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh
#   REQUIRE_READY=1 PY=... bash docs/talks/shape-v1/verify_snippets.sh     # before delivery
#
# Inputs: DEMO_DATA (default $BENCH_DATA_DIR/demo, from demo/make_data.py) and PROD_DATA
# (default $BENCH_OUT_DIR/reference_port/retail/medium/seed42, the "production" stand-in).
# Works in a scratch directory; writes nothing in the repo.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
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
echo
echo "######## PART 1: RUNNABLE NOW (asserted) ########"

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
assert len(rels) == 8   # NUMBERS.md N-76
assert shape.load("retail_prod.shape").to_dict() == p.to_dict()

# --- slide 11 ---------------------------------------------------------------
html = p.to_html()
assert "http://" not in html and "https://" not in html
print("report ok")

# --- slide 20: the claim that a raw profile contains real values ------------
import zipfile, pyarrow.parquet as pq
raw = zipfile.ZipFile("retail_prod.shape").read("profile.json").decode()
emails = [e for e in pq.read_table("prod/customer.parquet").column("email").to_pylist() if e]
found = sum(1 for e in emails[:5000] if e in raw)
print(f"raw .shape contains {found} of the first 5000 customer emails")
assert found > 0, "slide 20 says raw profiles contain real values; update the slide"
EOF

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

echo "== slide 19: CLI and exit codes"
run() { echo "\$ $*"; set +e; "$@" > out.txt 2>&1; rc=$?; set -e; head -c 300 out.txt; echo; echo "exit $rc"; }
expect() { [ "$rc" -eq "$1" ] || { echo "expected exit $1, got $rc"; exit 1; }; }
run "$SHAPE" profile orders_day1.parquet -o day1.shape --html day1.html --json day1.json; expect 0
run "$SHAPE" profile orders_day2.parquet -o day2.shape; expect 0
run "$SHAPE" check day1.shape contracts/orders.json; expect 0
run "$SHAPE" check day2.shape contracts/orders.json --json result.json; expect 1
run "$SHAPE" diff day1.shape day2.shape --fail-on-drift; expect 1
run "$SHAPE" check day1.shape contracts/no-such.json; expect 2

echo "== slide 22: generation at scale (reference generator; needs the pinned Spindle checkout)"
if [ -d "${SPINDLE_ROOT:-$HOME/spindle}" ]; then
    set +e
    BENCH_OUT_DIR="$WORK/gen" "$PY" "$REPO/benchmarks/vs_spindle/domain_1to1/generate.py" \
        --impl reference_port --domain retail --scale medium --seed 42 2>&1 | grep '^wrote' | tee gen.txt
    set -e
    grep -q '1,965,400 rows' gen.txt || { echo "generation did not write 1,965,400 rows"; exit 1; }
else
    echo "SKIPPED: no Spindle checkout at ${SPINDLE_ROOT:-$HOME/spindle}"
fi

echo
echo "######## PART 2: PENDING (READINESS.md) ########"
pending=0
report() {  # report ID STATUS DETAIL
    printf '%-8s %-8s %s\n' "$1" "$2" "$3"
    [ "$2" = READY ] || pending=$((pending + 1))
}

# R1 (P7-01): safe-profile validation command exists and accepts the prod profile
set +e; "$SHAPE" profile validate --safe retail_prod.shape >/dev/null 2>&1; rc=$?; set -e
if [ $rc -eq 0 ] || [ $rc -eq 1 ]; then report R1 READY "shape profile validate --safe (exit $rc)"; else report R1 PENDING "shape profile validate --safe -> exit $rc (P7-01)"; fi

# R2 (P7-01/P7-02): a safe export exists. Confirm the command when P7-01 lands, then make
# this check export the safe shape and assert it contains none of the source emails.
report R2 PENDING "safe export + 'no raw emails in safe .shape' assertion (P7-01, P7-02; command name set at P7-01)"

# R3 (P4-08): shape plan reads a profile artifact
set +e; "$SHAPE" plan retail_prod.shape >/dev/null 2>&1; rc=$?; set -e
if [ $rc -eq 0 ]; then report R3 READY "shape plan retail_prod.shape"; else report R3 PENDING "shape plan retail_prod.shape -> exit $rc (P4-08; today it crashes on a 0.9.0 artifact)"; fi

# R4 (P4-08): generate from a shape
if "$SHAPE" generate --help 2>/dev/null | grep -q -- '--from'; then report R4 READY "shape generate --from"; else report R4 PENDING "shape generate has no --from (P4-08)"; fi

# R5 (P4-09): fidelity report
if "$SHAPE" fidelity --help 2>/dev/null | grep -qi 'tier\|threshold'; then report R5 READY "shape fidelity"; else report R5 PENDING "shape fidelity is the legacy command (P4-09)"; fi

# R6 (P4-07/P4-10): the product engine generates retail
set +e; BENCH_OUT_DIR="$WORK/gen" "$PY" "$REPO/benchmarks/vs_spindle/domain_1to1/generate.py" \
    --impl shape --domain retail --scale small --seed 1042 >/dev/null 2>&1; rc=$?; set -e
if [ $rc -eq 0 ]; then report R6 READY "generate.py --impl shape"; else report R6 PENDING "generate.py --impl shape -> exit $rc (P4-07)"; fi

# R7 (G1, G4): product numbers in results.json
if "$PY" -c "import json,sys; d=json.load(open('$REPO/benchmarks/vs_spindle/results.json')); sys.exit(0 if d.get('shape') else 1)"; then
    report R7 READY "results.json has shape rows"; else report R7 PENDING "results.json: \"shape\": null (G1 profiling, G4 generation)"; fi

# R8 (PF-02): distributed profiling notebook
if grep -rqs mapInArrow "$REPO/integrations/fabric/notebooks/"; then report R8 READY "PySpark notebook uses mapInArrow"; else report R8 PENDING "no distributed PySpark profiling notebook (PF-02)"; fi

# R9 (PF-06): generation pipeline
if ls "$REPO"/integrations/fabric/pipelines/ | grep -qi gen; then report R9 READY "generation pipeline present"; else report R9 PENDING "no generate-then-profile pipeline (PF-06)"; fi

# R10: finding F1 (missing first .shape argument must exit 2)
set +e; "$SHAPE" check no-such.shape contracts/orders.json >/dev/null 2>&1; rc=$?; set -e
if [ $rc -eq 2 ]; then report R10 READY "missing .shape exits 2"; else report R10 PENDING "missing .shape exits $rc, not 2 (finding F1)"; fi

echo
echo "RUNNABLE-NOW SNIPPETS OK; $pending pending item(s)"
if [ "${REQUIRE_READY:-0}" = 1 ] && [ $pending -gt 0 ]; then
    echo "NOT READY TO DELIVER"; exit 1
fi
