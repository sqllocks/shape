cd /home/user/shape && source scripts/env.sh
D=$1; N=$2
G=benchmarks/vs_spindle/domain_1to1/generate.py
A="--impl shape --domain $D --scale medium --seed 1042"
t() { grep GEN_JSON | python3 -c "import sys,json; print(json.loads(sys.stdin.read()[9:])['total_s'])"; }
for i in $(seq $N); do
  echo "default $($SHAPE_VENV/bin/python $G $A | t)"
  echo "env_system $(ARROW_DEFAULT_MEMORY_POOL=system $SHAPE_VENV/bin/python $G $A | t)"
  echo "thp_off $($SHAPE_VENV/bin/python /tmp/wrap_thp.py $G $A | t)"
  echo "both $(ARROW_DEFAULT_MEMORY_POOL=system $SHAPE_VENV/bin/python /tmp/wrap_thp.py $G $A | t)"
done
