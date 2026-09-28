from shape.ci import evaluate_ci
baseline={"rows":1,"columns":{"x":{"kind":"numeric","mean":10}}}
candidate={"rows":1,"columns":{"x":{"kind":"numeric","mean":10.1}}}
report=evaluate_ci(baseline,candidate,max_drift=.1)
raise SystemExit(0 if report.passed else 1)
