# Integration preparation (no product merge yet)

All active source heads remain frozen for CI. Read-only git merge-tree previews wrote only Git objects and temporary reports; an exit1 here means predicted conflicts, not a failing acceptance test.

The streaming pair (metadata91ef620 + formatsd6dc9f3) conflicts in shared messages, Kafka/Event Hubs emitters, Event Hubs source, documentation/requirements and live/emulator test additions. The formats author is preparing an uncommitted detached temporary worktree; the metadata author independently reviews it. Preserve all Wanted behavior, assertions, tests, traceability and both Unreleased entries. The temporary result has no PR and will not be pushed. It provides a resolution patch and focused cross-feature evidence for the actual main refresh after the first streaming PR merges.

Other previews have no product-source conflicts:

| Pair | Predicted conflicts |
|---|---|
| Iceberg + Eventhouse | CHANGELOG, DBT and plugin-authoring literal inventories, requirements |
| Iceberg + semantic | requirements |
| Eventhouse + semantic | FABRIC_PLATFORM and requirements |
| dbt + semantic | requirements |

For the combined Iceberg/Eventhouse inventory, replay the actual documentation examples:179 existing registrations plus1 Eventhouse and2 Iceberg become182, with alphabetic rows. Preserve the dbt time-spine sample resource changes too. No transcript normalizer, assertion or gate changes are authorized.

Actual merging remains through the PR merge API as a merge commit, only after every applicable exact-head CI check is green, required local common acceptance passes, main is contained and independent review is APPROVED. Then update every other own open lane from main, retain both changelog entries and wait for fresh CI. Temporary integration preparation does not waive any merge gate.
