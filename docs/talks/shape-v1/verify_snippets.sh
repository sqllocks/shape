#!/usr/bin/env bash
# Runs every Shape API and CLI snippet that appears on a slide (SCRIPT.md, DEMO.md) against
# the installed `shape` package and asserts the results the talk quotes.
#
#   source scripts/env.sh && python demo/make_data.py --out "$BENCH_DATA_DIR/demo"
#   PY=~/.venvs/shape/bin/python bash docs/talks/shape-v1/verify_snippets.sh
#
# Needs: the demo data written by demo/make_data.py (DEMO_DATA, default
# $BENCH_DATA_DIR/demo or ~/bench-data/demo), and a Python with `pip install -e .` and
# `deltalake` (for the Delta snippet). Works in a scratch directory; writes nothing in the repo.
# Exit 0 means every snippet ran and printed what the slides say it prints.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
PY="${PY:-python}"
SHAPE="$(dirname "$("$PY" -c 'import sys; print(sys.executable)')")/shape"
DEMO_DATA="${DEMO_DATA:-${BENCH_DATA_DIR:-$HOME/bench-data}/demo}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

test -f "$DEMO_DATA/day1/orders.parquet" || { echo "no demo data in $DEMO_DATA (run demo/make_data.py)"; exit 2; }
cp "$DEMO_DATA/day1/orders.parquet" "$WORK/orders_day1.parquet"
cp "$DEMO_DATA/day2/orders.parquet" "$WORK/orders_day2.parquet"
mkdir -p "$WORK/contracts" && cp "$REPO"/demo/contracts/*.json "$WORK/contracts/"
cd "$WORK"

echo "== shape $("$PY" -c 'import shape; print(shape.__version__)') at $("$PY" -c 'import shape, os; print(os.path.dirname(shape.__file__))')"

echo "== Python snippets (slides 9 to 13)"
"$PY" - <<'EOF'
# --- slide 9: profile -------------------------------------------------------
import shape

p = shape.profile("orders_day1.parquet", name="orders")
shape.save(p, "orders_day1.shape")
col = p.summary()["columns"]["order_total"]
print(col)
assert col["dtype"] == "float" and col["max"] == 5135.63 and col["null_rate"] == 0.0

# --- slide 10: check, day 1 -------------------------------------------------
r = shape.check(p, "contracts/orders.json")
print(r.passed, r.violations)
assert (r.passed, r.violations) == (True, [])

# --- slide 11: check, day 2 -------------------------------------------------
today = shape.profile("orders_day2.parquet", name="orders")
r = shape.check(today, "contracts/orders.json")
print(r.passed)
for v in r.violations:
    print(v["column"], v["rule"], v["observed"])
assert r.passed is False
got = {(v["column"], v["rule"]) for v in r.violations}
assert got == {("status", "allowed_values"), ("order_total", "max")}, got
assert {"unexpected_values": ["lost"]} in [v["observed"] for v in r.violations]
assert 7189.882 in [v["observed"] for v in r.violations]

# --- slide 12: diff, default thresholds -------------------------------------
d = shape.diff(shape.load("orders_day1.shape"), today)
print(d.drifted, [(c["column"], c["kind"]) for c in d.changes])
assert d.drifted
assert ("order_total", "mean_shift") not in [(c["column"], c["kind"]) for c in d.changes]

# --- slide 12: diff with the demo threshold ---------------------------------
d = shape.diff(shape.load("orders_day1.shape"), today,
               thresholds={"mean_shift_std": 0.25})
for c in d.changes:
    print(c["column"], c["kind"], c["severity"])
kinds = [(c["column"], c["kind"], c["severity"]) for c in d.changes]
assert ("order_total", "mean_shift", "medium") in kinds
assert ("status", "new_categorical_values", "low") in kinds

# --- slide 13: the report ---------------------------------------------------
html = p.to_html()
assert html.lstrip().lower().startswith("<!doctype html") or "<html" in html.lower()
assert "http://" not in html and "https://" not in html   # self-contained, no external assets
print("report ok")
EOF

echo "== Delta table snippet (slide 27)"
"$PY" - <<'EOF'
import pyarrow.parquet as pq
from deltalake import write_deltalake
write_deltalake("Tables/orders_day1", pq.read_table("orders_day1.parquet"))

# --- slide 27: what the Fabric notebook does with a lakehouse table ---------
import shape
p = shape.profile("Tables/orders_day1", name="orders_day1")
r = shape.check(p, "contracts/orders.json")
print(p.summary()["name"], p.summary()["row_count"], r.passed)
assert p.summary()["columns"]["order_total"]["max"] == 5135.63 and r.passed
EOF

echo "== CLI snippets (slide 14)"
run() { echo "\$ $*"; set +e; "$@"; rc=$?; set -e; echo "exit $rc"; }
expect() { [ "$rc" -eq "$1" ] || { echo "expected exit $1, got $rc"; exit 1; }; }
run "$SHAPE" profile orders_day1.parquet -o day1.shape --html day1.html --json day1.json; expect 0
run "$SHAPE" profile orders_day2.parquet -o day2.shape; expect 0
run "$SHAPE" check day1.shape contracts/orders.json; expect 0
run "$SHAPE" check day2.shape contracts/orders.json --json result.json; expect 1
run "$SHAPE" diff day1.shape day2.shape --fail-on-drift; expect 1
run "$SHAPE" check day1.shape contracts/no-such.json; expect 2
test -s day1.html && test -s day1.json && test -s result.json

echo "== Known issue probe (reported, not asserted; see STATUS.md, finding F1)"
set +e; "$SHAPE" check no-such.shape contracts/orders.json >/dev/null 2>&1; rc=$?; set -e
echo "shape check <missing .shape> <contract>: exit $rc (the section 12.2 contract says 2)"

echo "ALL SNIPPETS OK"
