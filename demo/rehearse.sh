#!/usr/bin/env bash
# Rehearse the demo from scratch (docs/DEMO.md and the demo/TALK.md local fallback): a fresh
# venv from the wheels of this checkout, then every scripted step, timed, its output captured in
# RUN_DIR/out and RUN_DIR/steps.tsv (step, name, exit code, seconds, command). Linux; needs no
# Fabric credentials. Run it twice into two folders and diff the outputs for determinism
# (docs/plans/lane_status/DEMO-REHEARSAL.md). Usage: demo/rehearse.sh RUN_DIR
set -u
REPO=$(cd "$(dirname "$0")/.." && pwd)
RUN=${1:?usage: demo/rehearse.sh RUN_DIR (outside the repo)}
mkdir -p "$RUN" && RUN=$(cd "$RUN" && pwd)
rm -rf "$RUN"; mkdir -p "$RUN/wheels" "$RUN/out" "$RUN/w"
LOG=$RUN/steps.tsv
n=0
step() {  # step NAME CMD...
  n=$((n+1)); local name=$1; shift
  local f; f=$(printf '%s/out/%02d_%s.txt' "$RUN" "$n" "$name")
  local s e; s=$(date +%s.%N)
  ( cd "$RUN/w" && "$@" ) >"$f" 2>&1; local code=$?
  e=$(date +%s.%N)
  printf '%02d\t%s\t%s\t%.2f\t%s\n' "$n" "$name" "$code" "$(echo "$e - $s" | bc)" "$*" >>"$LOG"
}
# 0. build and install (docs/INSTALL.md, with the branch's wheels instead of PyPI)
step build_core python3 "$REPO/scripts/build_pure_wheel.py" --out "$RUN/wheels"
step build_plugins pip wheel -q --no-deps -w "$RUN/wheels" "$REPO/plugins/shape-domains" \
  "$REPO/plugins/shape-fabric" "$REPO/plugins/shape-eventhubs" "$REPO/plugins/shape-sqlserver"
step venv python3.11 -m venv "$RUN/venv"
step pip_upgrade "$RUN/venv/bin/python" -m pip install -q --upgrade pip
step install "$RUN/venv/bin/pip" install -q "$RUN"/wheels/*.whl
step install_faker "$RUN/venv/bin/pip" install -q faker
export PATH="$RUN/venv/bin:$PATH" SHAPE_HOME="$RUN/w/home"
step doctor shape doctor
step conformance shape conformance
# A. shape demo (docs/DEMO.md quick start)
step demo_list shape demo list
step run_inference shape demo run retail --rows 1000
step init shape demo init --name here --local-path ./landing
step run_seeding shape demo run retail --mode seeding --connection here --rows 1000
SID=$(grep -o 'Session: [0-9a-f]*' "$RUN"/out/*_run_seeding.txt | awk '{print $2}')
step status shape demo status "$SID"
step report shape demo report "$SID" --format html --output report.html
step report_md shape demo report "$SID"
step cleanup_dry shape demo cleanup "$SID" --dry-run
step cleanup shape demo cleanup "$SID"
step notebook shape demo notebook retail --mode seeding --output retail.ipynb
step preflight shape demo preflight
step dry_run shape demo run retail --dry-run
step estimate shape demo run retail --estimate
step seeding_dry_run shape demo run retail --mode seeding --connection here --dry-run
step streaming shape demo run retail --mode streaming --max-events 5
step dry_run_1000 shape demo run retail --rows 1000 --dry-run
step outputs_all shape demo run retail --rows 1000 --output all --output-dir charts
step adventureworks shape demo run adventureworks --rows 1000
step healthcare shape demo run healthcare --rows 1000
step healthcare_stream shape demo run healthcare --mode streaming --max-events 3
step enterprise shape demo run enterprise --mode seeding --rows 1000 --connection here
step python_api python -c '
from shape.demo import demo_run, demo_status, demo_cleanup
result = demo_run({"scenario": "retail", "mode": "seeding", "rows": 1000})
print(len(demo_status(result["session_id"])["manifest"]["artifacts"]), "artifacts")
print(demo_cleanup(result["session_id"])["ok"])'
# B. talk kit (demo/TALK.md): data, the local fallback, the stage diff
step make_data python "$REPO/demo/make_data.py" --out data
C=$REPO/demo/contracts
# --capture full, as TALK.md says: the orders contract checks order_total's min and max, which
# the default (safe) capture does not keep, so the check could not evaluate them (exit 2)
step profile_day1 shape profile data/day1/orders.parquet -o o1.shape --html o1.html --capture full
step check_day1 shape check o1.shape "$C/orders.json"
step profile_day2 shape profile data/day2/orders.parquet -o o2.shape --html o2.html --capture full
step check_day2 shape check o2.shape "$C/orders.json"
step diff_stage shape diff o1.shape o2.shape --mean-shift-std 0.25 --min-severity medium
step diff_plain_bytes sh -c 'shape diff o1.shape o2.shape 2>/dev/null | wc -c'
for t in customers products; do
  step "profile_${t}_1" shape profile "data/day1/$t.parquet" -o "${t}1.shape"
  step "profile_${t}_2" shape profile "data/day2/$t.parquet" -o "${t}2.shape"
  step "check_${t}_1" shape check "${t}1.shape" "$C/$t.json"
  step "check_${t}_2" shape check "${t}2.shape" "$C/$t.json"
done
step api_notebook_beat python -c '
import json, shape
p = shape.profile("data/day1/orders.parquet", name="orders_day1")
s = p.summary(); print(s["row_count"], len(s["columns"]))
r = shape.check(p, "'"$C"'/orders.json"); print(json.dumps(r.to_dict(), sort_keys=True))
q = shape.profile("data/day2/orders.parquet", name="orders_day2")
print(json.dumps(shape.check(q, "'"$C"'/orders.json").to_dict(), sort_keys=True))
d = shape.diff(p, q, thresholds={"mean_shift_std": 0.25})
print(d.drifted, sorted({(c["column"], c["kind"]) for c in d.changes}))
print(len(p.to_html()) > 1000)'
cat "$LOG"
# TALK.md's "Nothing works" fallback: day 1 passes (exit 0), day 2 fails (exit 1)
awk -F'\t' '$2 == "check_day1" && $3 != 0 || $2 == "check_day2" && $3 != 1 {
  print "fallback check " $2 " exited " $3 > "/dev/stderr"; bad = 1 } END { exit bad }' "$LOG"
