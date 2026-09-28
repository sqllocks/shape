# Live Connector Qualification

Run the same logical fixture through direct columnar input and each live connector. Assert:
1. row count and schema equivalence;
2. Shape evidence equivalence within sketch tolerances;
3. drift/quality alert equivalence;
4. checkpoint restart with injected consumer termination;
5. duplicate/redelivery policy;
6. partition reordering policy;
7. bounded keyed state under high cardinality;
8. sustained throughput and p50/p95/p99 batch latency;
9. TLS/auth failure is fail-closed;
10. secrets never appear in Shape/history/checkpoint artifacts.

No live-throughput claim is permitted until this suite runs against the named broker/service and records configuration + host.
