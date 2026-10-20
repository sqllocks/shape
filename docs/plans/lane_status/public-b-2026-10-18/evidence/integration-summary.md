# Recovered streaming integration preparation

This is an uncommitted detached preview, not a product branch or merge. Actual lanes absorb each other only through current main after a PR merge. All normal acceptance and exact-head CI gates remain required.

- Binary lane: 7a0316cc0ac18ae8b9fe283446f449325b798083 (signed/pushed standalone dead-letter correction).
- Metadata lane: 91ef6200266bf962246d1553356b26a442be2303 (frozen).
- Persistent preview: /workspace/public-b-stream-integration, detached at the binary lane.
- Resolved staged tree: 363da4067db65b3ec66e02b27d18075b9b1dafaf.
- Prior reviewed tree: d6d5ec550bfbe9295b5273a2e285374c7671f436. Product source bytes remain identical; only the two new standalone regression functions and updated binary-lane evidence were added.

The preview preserves both Wanted lists, their documentation/traceability and all tests. The combination-only Kafka key-option collision is fixed by retaining a separate event_key local through multiple batches. Canonical binary metadata and rejected-row alignment remain intact. The dead-letter reason positional bug also existed in the standalone binary lane and is now fixed there at 7a0316c; no known standalone defect is deliberately deferred.

Independent metadata reviewer APPROVED the exact new staged tree. All four live/emulator files retain AST-identical tests/helpers/classes versus both source branches (eight comparisons). Independent refreshed selection:14 passed,53 deselected,exit0; product mapping preserved; no gate or assertion weakened.

Recovered broad metadata/binary selection:275 passed,38 service deselections in each kernel mode (native14.08s,Python38.27s). Remote-confirmation CLI tests are a separate selection:70 passed in each kernel mode (native1.75s,Python1.73s), recorded in integration-cli-{native,python}.log. These are focused integration results, not full common acceptance or live passes.

Artifacts:

- stream-integration-resolution.patch compares virtual auto-merge tree524c9085b2a420c8f0a9f25858992a004b4576d6 with the approved resolved tree. The virtual preview exit1 denotes expected source/doc conflicts. Conflict-marker branch labels are specific to the two pinned heads and may differ from an actual origin/main merge; preserve any intervening main changes and review the resulting files.
- stream-integration-from-binary.patch reconstructs the entire candidate from exact binary head7a0316c. It is a recovery artifact, not a patch to apply blindly over a later main.
- integration-objects.json records pinned objects and reviewed product hashes.
- commands.jsonl and per-command logs retain exact commands, exits and zombie counts.

After an actual first streaming PR merge: update other open lanes from main under lead coordination, resolve conflicts while retaining both changelog/requirement entries and all intervening main behavior, run combined focused tests, get independent review bound to the actual signed head, and wait for every current CI job before merging.
