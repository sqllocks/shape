# Shape architecture at local completion

Shape is a local-first data-behavior platform.

`data -> capture/profile -> .shape -> query / contract / quality / drift / registry / history / generate`

A Shape is content-addressed evidence describing data behavior. It may be versioned over time, constrained by contracts, compared for compatibility/drift, safely queried without raw rows, released under classification policy, stored in an immutable registry, and compiled into synthetic data. Domains add reusable semantic behavior such as coherent addresses and locations.

The system deliberately distinguishes:
- evidence captured from data;
- policy applied to evidence;
- reconstruction capabilities;
- reconstruction fidelity;
- external qualification evidence.

No subsystem may claim evidence it did not capture, fidelity it did not measure, or external qualification that did not run.
