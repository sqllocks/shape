# Eventhouse recovery and independent reviews

Own Eventhouse head a7e450ad105a71c334d00eeed1c8b7291aac6153 remains unchanged/clean. Current hosted applicable checks are green; local full common acceptance remains pending. Old temporary queues disappeared in restart and are not passing evidence. No full run, Docker build, bootstrap, live test, merge or issue closure was performed here.

Fresh own focused source tests pass native46 (0.74s) and Python46 (0.50s), each one existing unsigned-artifact warning, exit0, zombies0 before/after.

Formats correction APPROVED exact signed7a0316cc0ac18ae8b9fe283446f449325b798083. Canonical event-key mapping preserves dead-letter reason after binary refusal. Independent native2 (0.62s) and Python2 (0.58s) pass. No old tests/assertions/gates changed.

Iceberg local FileIO adapter APPROVED exact pushed commit be2f6727a563fbb830dcce2d3685da180506ae0a (Signed-off-by present). Committed source/test blobs match the final reviewed and independently tested worktree; only additional delta is lane evidence. Final independent 16 tests pass (1.22s), exit 0, zombies 0 before/after; log iceberg-local-fileio-sixteen.log. The filesystem boundary adapter covers old SDK scan paths, named local catalogs, Windows path conversion units, encoded directories, literal percent identifiers, and real local write/read placement. Generated SQLite URI escapes its native path once. Explicit custom IO, other SDK filesystem schemes, credential handling, published dependency floor, existing assertions and gates are preserved. Actual Windows execution remains pending hosted CI.

Reviewed Iceberg blobs: _iceberg.py 98f42bd020a0f47952919a693f9dbf2fad522430; _iceberg_local_io.py 3c4a301c8cde2b8535ba9d11078a24e26c5f4431; test_iceberg_local_io.py 383454089b219f0de17720323ff5d6e111a2357c.

All command/exit evidence and logs are in this directory.
