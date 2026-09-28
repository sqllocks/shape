# RQ-1 Statistical Correctness Evidence

Five deterministic 5,000-row Gaussian trials (population mean 10, sigma 3) produced absolute mean errors between ~0.0004 and ~0.0463 and approximate median errors between ~0.0022 and ~0.0818.

Partition merge-order harness:
- mean delta: ~5.68e-14
- HLL distinct-estimate delta: 0
- reference KLL q50 delta: 1.7

Conclusion: exact/merge-stable statistics and approximate/order-sensitive sketches must remain explicitly distinguished. Error metadata is now emitted for approximate cardinality/quantile evidence.
